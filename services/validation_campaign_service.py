"""유효한 microstructure 캡처 날짜로 전략 검증 캠페인을 구성·집계한다."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable, Iterable


class ValidationCampaignService:
    def __init__(
        self,
        *,
        replay_dir: str | Path,
        strategies: Iterable[str],
        strategy_runner: Callable[[str, list[str], Path], dict] | None = None,
        quality_report_provider: Callable[[Path], dict] | None = None,
        walk_forward_train_days: int = 20,
        walk_forward_tune_days: int = 5,
        walk_forward_test_days: int = 5,
    ) -> None:
        self._replay_dir = Path(replay_dir)
        self._strategies = tuple(strategies)
        self._runner = strategy_runner
        self._quality_report_provider = quality_report_provider or self._compute_quality_report
        self._walk_forward_required = (
            int(walk_forward_train_days)
            + int(walk_forward_tune_days)
            + int(walk_forward_test_days)
        )

    def build_plan(self) -> dict:
        quality = self._quality_report_provider(self._replay_dir)
        valid_dates = sorted(
            str(date)
            for date, row in (quality.get("by_date") or {}).items()
            if row.get("quality_gate_passed") is True
        )
        available = len(valid_dates)
        walk_forward_ready = available >= self._walk_forward_required
        quality_totals = {
            key: value
            for key, value in (quality.get("totals") or {}).items()
            if not isinstance(value, dict)
        }
        return {
            "replay_dir": str(self._replay_dir),
            "valid_dates": valid_dates,
            "valid_date_count": available,
            "strategy_count": len(self._strategies),
            "strategies": list(self._strategies),
            "quality_totals": quality_totals,
            "walk_forward": {
                "ready": walk_forward_ready,
                "required_dates": self._walk_forward_required,
                "available_dates": available,
                "status": "ready" if walk_forward_ready else "waiting_for_more_valid_dates",
            },
        }

    def run(self) -> dict:
        plan = self.build_plan()
        if not plan["valid_dates"]:
            return {"status": "blocked", "plan": plan, "summary": {}, "strategies": []}
        if self._runner is None:
            raise RuntimeError("strategy_runner가 필요합니다")

        rows: list[dict] = []
        for strategy in self._strategies:
            try:
                payload = self._runner(strategy, plan["valid_dates"], self._replay_dir)
                rows.append(self._summarize(strategy, payload))
            except Exception as exc:
                rows.append({"strategy": strategy, "status": "failed", "error": str(exc)})
        return {
            "status": "completed",
            "plan": plan,
            "summary": {
                "completed": sum(row.get("status") == "completed" for row in rows),
                "failed": sum(row.get("status") == "failed" for row in rows),
                "zero_trade": sum(row.get("zero_trade") is True for row in rows),
            },
            "strategies": rows,
        }

    @staticmethod
    def _summarize(strategy: str, payload: dict) -> dict:
        executions = payload.get("execution_reports") or []
        journal = payload.get("journal_records") or []
        buys = [row for row in executions if row.get("side") == "BUY" and int(row.get("filled_qty") or 0) > 0]
        sells = [row for row in executions if row.get("side") == "SELL" and int(row.get("filled_qty") or 0) > 0]
        reasons = Counter(
            str(row.get("reason") or "unknown")
            for row in journal
            if str(row.get("status") or "").upper() == "REJECTED"
        )
        zero_trade = not buys
        if not zero_trade:
            zero_reason = None
        elif reasons:
            zero_reason = "all_observed_signals_rejected"
        else:
            zero_reason = "no_signal_or_candidate_observed"
        portfolio = payload.get("portfolio") or {}
        return {
            "strategy": strategy,
            "status": "completed",
            "buy_fills": len(buys),
            "sell_fills": len(sells),
            "rejected_records": sum(reasons.values()),
            "rejection_reasons": dict(sorted(reasons.items())),
            "open_positions": len(portfolio.get("positions") or {}),
            "realized_net_pnl": float(portfolio.get("realized_net_pnl") or 0),
            "zero_trade": zero_trade,
            "zero_trade_reason": zero_reason,
        }

    @staticmethod
    def _compute_quality_report(replay_dir: Path) -> dict:
        from scripts.analyze_backtest_microstructure_quality import (
            compute_quality_report,
            load_capture_payloads,
        )

        return compute_quality_report(load_capture_payloads(replay_dir))
