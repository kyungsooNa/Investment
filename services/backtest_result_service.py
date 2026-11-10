"""저장된 백테스트 journal을 화면용 성과 요약으로 변환한다."""
from __future__ import annotations

from typing import Any


class BacktestResultService:
    MIN_COMPLETED_TRADES = 30

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def list_results(self, *, limit: int | None = 50) -> list[dict[str, Any]]:
        return [
            self._summarize(run, self._repository.load_records(run.get("run_id", "")))
            for run in self._repository.list_runs(limit=limit)
        ]

    def _summarize(self, run: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        portfolio = metadata.get("portfolio") if isinstance(metadata.get("portfolio"), dict) else {}
        dates = [str(value) for value in metadata.get("dates", []) if value]
        completed = [record for record in records if str(record.get("status") or "").upper() == "SOLD"]
        wins = [record for record in completed if _number(record.get("net_pnl")) > 0]
        net_pnls = [_number(record.get("net_pnl")) for record in completed]
        net_returns = [
            _number(record.get("net_return"))
            for record in completed
            if record.get("net_return") is not None
        ]
        gains = sum(value for value in net_pnls if value > 0)
        losses = abs(sum(value for value in net_pnls if value < 0))
        initial_cash = _number(portfolio.get("initial_cash") or metadata.get("initial_cash"))
        realized_net_pnl = _number(portfolio.get("realized_net_pnl"))
        if not portfolio or portfolio.get("realized_net_pnl") is None:
            realized_net_pnl = sum(net_pnls)
        positions = portfolio.get("positions") if isinstance(portfolio.get("positions"), dict) else {}

        return {
            "run_id": str(run.get("run_id") or ""),
            "strategy": str(run.get("strategy") or ""),
            "target_date": str(run.get("target_date") or ""),
            "start_date": dates[0] if dates else _target_date_part(run.get("target_date"), 0),
            "end_date": dates[-1] if dates else _target_date_part(run.get("target_date"), -1),
            "saved_at": str(run.get("saved_at") or ""),
            "date_count": int(metadata.get("date_count") or len(dates)),
            "record_count": int(run.get("record_count") or len(records)),
            "completed_trade_count": len(completed),
            "signal_count": sum(
                1
                for record in records
                if str(record.get("side") or "").upper() == "BUY"
                and str(record.get("status") or "").upper() == "FILLED"
            ),
            "rejected_count": sum(
                1 for record in records if str(record.get("status") or "").upper() == "REJECTED"
            ),
            "open_position_count": len(positions),
            "initial_cash": initial_cash,
            "realized_net_pnl": realized_net_pnl,
            "realized_return_pct": (realized_net_pnl / initial_cash * 100) if initial_cash else None,
            "win_rate": (len(wins) / len(completed) * 100) if completed else None,
            "avg_net_return": (sum(net_returns) / len(net_returns)) if net_returns else None,
            "profit_factor": (gains / losses) if losses else (None if gains else 0.0),
            "validity": self._validity(metadata, len(completed)),
        }

    def _validity(self, metadata: dict[str, Any], completed_count: int) -> dict[str, str]:
        if metadata.get("audit_type"):
            return _validity("AUDIT_ONLY", "일일 감사 결과이며 장기 수익성 백테스트가 아닙니다.")

        declared = str(metadata.get("validity_status") or "").upper()
        declared_reason = str(metadata.get("validity_reason") or "").strip()
        if declared in {"INVALID", "DATA_INCOMPLETE", "LOOKAHEAD_RISK"}:
            return _validity(declared, declared_reason or "실행 메타데이터에서 결과 사용이 제한되었습니다.")
        if metadata.get("data_complete") is False:
            return _validity("DATA_INCOMPLETE", declared_reason or "필수 과거 데이터가 완전하지 않습니다.")
        if completed_count < self.MIN_COMPLETED_TRADES:
            return _validity(
                "INSUFFICIENT_SAMPLE",
                f"완료 거래 {completed_count}건으로 최소 {self.MIN_COMPLETED_TRADES}건보다 적습니다.",
            )
        if declared == "VALID":
            return _validity("VALID", declared_reason or "데이터 및 시점 유효성 검증을 통과했습니다.")
        return _validity("UNVERIFIED", "PIT·데이터 완전성 검증 정보가 없어 수익성을 확정할 수 없습니다.")


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _target_date_part(value: Any, index: int) -> str:
    parts = [part for part in str(value or "").split("_") if part]
    return parts[index] if parts else ""


def _validity(status: str, reason: str) -> dict[str, str]:
    labels = {
        "VALID": "유효",
        "UNVERIFIED": "미검증",
        "INSUFFICIENT_SAMPLE": "표본 부족",
        "DATA_INCOMPLETE": "데이터 부족",
        "LOOKAHEAD_RISK": "미래참조 위험",
        "INVALID": "무효",
        "AUDIT_ONLY": "감사 전용",
    }
    return {"status": status, "label": labels.get(status, status), "reason": reason}
