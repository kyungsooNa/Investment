"""Backtest strategy coverage for daily new-high stocks."""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from common.trade_journal_schema import normalize_backtest_decision
from services.backtest_replay_adapter import StockQueryBacktestReplayService
from services.backtest_replay_context import BacktestMarketClock
from strategies.debug.strategy_debug_runner import StrategyDebugRunner


_DEFAULT_STRATEGY_KEYS = (
    "oneil_pocket_pivot",
    "oneil_squeeze_breakout",
    "high_tight_flag",
    "first_pullback",
    "larry_williams_vbo",
    "rsi2_pullback",
    "larry_williams_channel_breakout",
)


@dataclass
class StrategyCoverageSummary:
    strategy_key: str
    strategy_name: str
    candidate_count: int
    bought_count: int = 0
    not_bought_count: int = 0
    rejected_count: int = 0
    missing_from_universe_count: int = 0
    no_signal_count: int = 0
    data_unavailable_count: int = 0
    not_bought_rate: float = 0.0
    evaluable_not_bought_rate: float = 0.0
    saved_run: dict[str, Any] | None = None


@dataclass
class NewHighStrategyCoverageBacktestResult:
    target_date: str
    skipped: bool = False
    skip_reason: str = ""
    newhigh_count: int = 0
    strategy_count: int = 0
    all_strategy_missed_count: int = 0
    all_strategy_missed_rate: float = 0.0
    strategy_summaries: list[StrategyCoverageSummary] = field(default_factory=list)
    saved_runs: list[dict[str, Any]] = field(default_factory=list)


class NewHighStrategyCoverageBacktestService:
    """Run active backtest strategies against only the day's new-high universe."""

    def __init__(
        self,
        *,
        stock_repository: Any,
        stock_query_service: Any,
        universe_service: Any,
        indicator_service: Any,
        market_clock: Any,
        backtest_journal_repository: Any,
        program_provider: Any | None = None,
        strategy_factory: Callable[..., Any] | None = None,
        env: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._stock_repository = stock_repository
        self._sqs = stock_query_service
        self._universe_service = universe_service
        self._indicator_service = indicator_service
        self._market_clock = market_clock
        self._repo = backtest_journal_repository
        self._program_provider = program_provider
        self._strategy_factory = strategy_factory or self._default_strategy_factory
        self._env = env
        self._logger = logger or logging.getLogger(__name__)
        self._debug_logger = logger if isinstance(logger, logging.Logger) else logging.getLogger(__name__)

    async def run(
        self,
        target_date: str,
        *,
        strategy_keys: list[str] | tuple[str, ...] | None = None,
        backtest_time: str = "15:30:00",
    ) -> NewHighStrategyCoverageBacktestResult:
        target_date = str(target_date)
        result = NewHighStrategyCoverageBacktestResult(target_date=target_date)
        if self._is_paper_mode():
            result.skipped = True
            result.skip_reason = "historical_intraday_unavailable_in_paper"
            return result

        newhigh_rows = await self._stock_repository.get_newhigh_stocks(target_date)
        candidate_codes = _extract_codes(newhigh_rows)
        result.newhigh_count = len(candidate_codes)
        if not candidate_codes:
            result.skipped = True
            result.skip_reason = "no_newhigh_stocks"
            return result

        selected_strategy_keys = list(strategy_keys or _DEFAULT_STRATEGY_KEYS)
        backtest_clock = BacktestMarketClock.from_clock(
            self._market_clock,
            default_time=backtest_time,
        )
        replay_sqs = StockQueryBacktestReplayService(
            self._sqs,
            program_provider=self._program_provider,
            market_clock=backtest_clock,
        )
        signal_time = _signal_time(target_date, backtest_time)
        all_bought_codes: set[str] = set()

        with tempfile.TemporaryDirectory(prefix="newhigh_strategy_coverage_") as tmp_dir:
            for strategy_key in selected_strategy_keys:
                summary, bought_codes = await self._run_strategy_coverage(
                    strategy_key=strategy_key,
                    target_date=target_date,
                    signal_time=signal_time,
                    candidate_codes=candidate_codes,
                    replay_sqs=replay_sqs,
                    backtest_clock=backtest_clock,
                    state_dir=tmp_dir,
                )
                result.strategy_summaries.append(summary)
                result.saved_runs.append(summary.saved_run or {})
                all_bought_codes.update(bought_codes)

        result.strategy_count = len(result.strategy_summaries)
        result.all_strategy_missed_count = len(set(candidate_codes) - all_bought_codes)
        result.all_strategy_missed_rate = _ratio(result.all_strategy_missed_count, result.newhigh_count)
        return result

    async def _run_strategy_coverage(
        self,
        *,
        strategy_key: str,
        target_date: str,
        signal_time: str,
        candidate_codes: list[str],
        replay_sqs: StockQueryBacktestReplayService,
        backtest_clock: BacktestMarketClock,
        state_dir: str,
    ) -> tuple[StrategyCoverageSummary, set[str]]:
        replay_sqs.set_backtest_date(target_date)
        backtest_clock.set_backtest_date(target_date)
        strategy_name = strategy_key
        try:
            strategy = self._strategy_factory(
                strategy_key=strategy_key,
                replay_sqs=replay_sqs,
                universe_service=self._universe_service,
                indicator_service=self._indicator_service,
                backtest_clock=backtest_clock,
                state_dir=state_dir,
                logger=self._debug_logger,
            )
            strategy_name = str(getattr(strategy, "name", strategy_key) or strategy_key)
            report = await StrategyDebugRunner(
                strategy,
                self._debug_logger,
                target_date=target_date,
                target_signal_time=signal_time,
            ).run(candidate_codes=candidate_codes)
            records = self._annotate_and_fill_records(
                records=report.journal_records,
                candidate_codes=candidate_codes,
                strategy_key=strategy_key,
                strategy_name=strategy_name,
                signal_time=signal_time,
                target_date=target_date,
            )
        except Exception as exc:
            self._logger.warning(
                "new-high strategy coverage failed: strategy=%s date=%s error=%s",
                strategy_key,
                target_date,
                exc,
                exc_info=True,
            )
            records = [
                self._synthetic_record(
                    code=code,
                    strategy=strategy_name,
                    signal_time=signal_time,
                    target_date=target_date,
                    strategy_key=strategy_key,
                    rejected_reason=f"data_unavailable:{exc}",
                    coverage_status="data_unavailable",
                )
                for code in candidate_codes
            ]

        summary, bought_codes = self._summarize_records(
            records,
            strategy_key=strategy_key,
            strategy_name=strategy_name,
            candidate_count=len(candidate_codes),
        )
        saved = self._repo.save_run(
            records,
            run_id=f"newhigh_coverage_{strategy_key}_{target_date}",
            strategy=strategy_name,
            target_date=target_date,
            metadata={
                "audit_type": "newhigh_strategy_coverage",
                "strategy_key": strategy_key,
                "candidate_count": summary.candidate_count,
                "bought_count": summary.bought_count,
                "not_bought_count": summary.not_bought_count,
                "rejected_count": summary.rejected_count,
                "missing_from_universe_count": summary.missing_from_universe_count,
                "no_signal_count": summary.no_signal_count,
                "data_unavailable_count": summary.data_unavailable_count,
                "not_bought_rate": summary.not_bought_rate,
                "evaluable_not_bought_rate": summary.evaluable_not_bought_rate,
            },
        )
        summary.saved_run = saved
        return summary, bought_codes

    def _annotate_and_fill_records(
        self,
        *,
        records: list[dict],
        candidate_codes: list[str],
        strategy_key: str,
        strategy_name: str,
        signal_time: str,
        target_date: str,
    ) -> list[dict]:
        annotated: list[dict] = []
        seen_codes: set[str] = set()
        candidate_set = set(candidate_codes)
        for record in records:
            code = _normalize_code(record.get("code"))
            if not code or code not in candidate_set:
                continue
            seen_codes.add(code)
            annotated.append(
                self._with_coverage_metadata(
                    record,
                    target_date=target_date,
                    strategy_key=strategy_key,
                    coverage_status=_coverage_status(record),
                )
            )

        for code in candidate_codes:
            if code in seen_codes:
                continue
            annotated.append(
                self._synthetic_record(
                    code=code,
                    strategy=strategy_name,
                    signal_time=signal_time,
                    target_date=target_date,
                    strategy_key=strategy_key,
                    rejected_reason="no_signal",
                    coverage_status="no_signal",
                )
            )
        return annotated

    def _summarize_records(
        self,
        records: list[dict],
        *,
        strategy_key: str,
        strategy_name: str,
        candidate_count: int,
    ) -> tuple[StrategyCoverageSummary, set[str]]:
        bought_codes = {
            str(record.get("code") or "")
            for record in records
            if _coverage_status(record) == "bought"
        }
        rejected_count = sum(1 for record in records if _coverage_status(record) == "rejected")
        missing_count = sum(1 for record in records if _coverage_status(record) == "missing_from_universe")
        no_signal_count = sum(1 for record in records if _coverage_status(record) == "no_signal")
        data_unavailable_count = sum(1 for record in records if _coverage_status(record) == "data_unavailable")
        not_bought_count = rejected_count + missing_count + no_signal_count
        evaluable_count = max(candidate_count - data_unavailable_count, 0)
        return (
            StrategyCoverageSummary(
                strategy_key=strategy_key,
                strategy_name=strategy_name,
                candidate_count=candidate_count,
                bought_count=len(bought_codes),
                not_bought_count=not_bought_count,
                rejected_count=rejected_count,
                missing_from_universe_count=missing_count,
                no_signal_count=no_signal_count,
                data_unavailable_count=data_unavailable_count,
                not_bought_rate=_ratio(not_bought_count, candidate_count),
                evaluable_not_bought_rate=_ratio(not_bought_count, evaluable_count),
            ),
            bought_codes,
        )

    @staticmethod
    def _with_coverage_metadata(
        record: dict,
        *,
        target_date: str,
        strategy_key: str,
        coverage_status: str,
    ) -> dict:
        copied = dict(record)
        metadata = dict(copied.get("metadata") or {})
        metadata["audit_type"] = "newhigh_strategy_coverage"
        metadata["newhigh_coverage_status"] = coverage_status
        metadata["target_date"] = target_date
        metadata["strategy_key"] = strategy_key
        copied["metadata"] = metadata
        return copied

    def _synthetic_record(
        self,
        *,
        code: str,
        strategy: str,
        signal_time: str,
        target_date: str,
        strategy_key: str,
        rejected_reason: str,
        coverage_status: str,
    ) -> dict:
        record = normalize_backtest_decision(
            {
                "signal_time": signal_time,
                "rejected_reason": rejected_reason,
                "strategy": strategy,
            },
            stock_code=code,
            strategy=strategy,
            accepted=False,
        )
        return self._with_coverage_metadata(
            record,
            target_date=target_date,
            strategy_key=strategy_key,
            coverage_status=coverage_status,
        )

    def _default_strategy_factory(self, **kwargs):
        from scripts.run_backtest import _build_backtest_strategy

        return _build_backtest_strategy(
            strategy_key=kwargs["strategy_key"],
            replay_sqs=kwargs["replay_sqs"],
            universe_service=kwargs["universe_service"],
            indicator_service=kwargs.get("indicator_service"),
            backtest_clock=kwargs["backtest_clock"],
            state_dir=kwargs["state_dir"],
            logger=kwargs.get("logger"),
        )

    def _is_paper_mode(self) -> bool:
        return bool(getattr(self._env, "is_paper_trading", False))


def _extract_codes(rows: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        code = _normalize_code(row.get("code") if isinstance(row, dict) else row)
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) < 6:
        return text.zfill(6)
    return text


def _coverage_status(record: dict) -> str:
    metadata = record.get("metadata") if isinstance(record, dict) else None
    if isinstance(metadata, dict) and metadata.get("newhigh_coverage_status"):
        return str(metadata.get("newhigh_coverage_status"))
    status = str(record.get("status") or "").upper()
    side = str(record.get("side") or "").upper()
    rejected_reason = str(record.get("rejected_reason") or "")
    if status == "SIGNAL" or side == "BUY":
        return "bought"
    if rejected_reason.startswith("data_unavailable"):
        return "data_unavailable"
    if rejected_reason == "missing_from_universe":
        return "missing_from_universe"
    if rejected_reason == "no_signal":
        return "no_signal"
    return "rejected"


def _signal_time(target_date: str, backtest_time: str) -> str:
    return (
        f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]} "
        f"{str(backtest_time or '15:30:00')[:8]}"
    )


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0
