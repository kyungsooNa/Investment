"""Post-market replay audit for scheduler missed-signal diagnosis."""
from __future__ import annotations

import gzip
import glob
import json
import logging
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from common.trade_journal_schema import normalize_backtest_decision
from services.backtest_replay_adapter import StockQueryBacktestReplayService
from services.backtest_replay_context import BacktestMarketClock
from strategies.debug.strategy_debug_runner import StrategyDebugRunner


_STRATEGY_NAME_RE = re.compile(r"^\d{8}_(?:\d{6}_)?(.+?)(?:_\d+)?\.log\.json.*$")

_STRATEGY_KEY_BY_NAME = {
    "OneilPocketPivot": "oneil_pocket_pivot",
    "OneilSqueezeBreakout": "oneil_squeeze_breakout",
    "HighTightFlag": "high_tight_flag",
    "FirstPullback": "first_pullback",
    "LarryWilliamsVBO": "larry_williams_vbo",
    "RSI2Pullback": "rsi2_pullback",
    "LarryWilliamsCB": "larry_williams_channel_breakout",
    "LarryWilliamsChannelBreakout": "larry_williams_channel_breakout",
}


@dataclass
class _AuditInput:
    candidates: set[str] = field(default_factory=set)
    scan_times: set[str] = field(default_factory=set)
    live_buy_times: dict[str, str] = field(default_factory=dict)


@dataclass
class PostMarketReplayAuditResult:
    target_date: str
    skipped: bool = False
    skip_reason: str = ""
    strategy_count: int = 0
    saved_runs: list[dict[str, Any]] = field(default_factory=list)
    missed_count: int = 0
    late_count: int = 0
    missing_from_universe_count: int = 0
    replayed_rejected_count: int = 0
    data_unavailable_count: int = 0


class PostMarketReplayAuditService:
    """Replay the day's live candidate set at actual scheduler scan times."""

    def __init__(
        self,
        *,
        stock_query_service: Any,
        universe_service: Any,
        indicator_service: Any,
        market_clock: Any,
        backtest_journal_repository: Any,
        scheduler_store: Any | None = None,
        virtual_trade_service: Any | None = None,
        log_dir: str = "logs/strategies",
        program_provider: Any | None = None,
        strategy_factory: Callable[..., Any] | None = None,
        env: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sqs = stock_query_service
        self._universe_service = universe_service
        self._indicator_service = indicator_service
        self._market_clock = market_clock
        self._repo = backtest_journal_repository
        if scheduler_store is None:
            raise ValueError("scheduler_store must be provided by the composition root")
        self._scheduler_store = scheduler_store
        self._virtual_trade_service = virtual_trade_service
        self._log_dir = log_dir
        self._program_provider = program_provider
        self._strategy_factory = strategy_factory or self._default_strategy_factory
        self._env = env
        self._logger = logger or logging.getLogger(__name__)
        self._debug_logger = logger if isinstance(logger, logging.Logger) else logging.getLogger(__name__)

    async def run(self, target_date: str) -> PostMarketReplayAuditResult:
        result = PostMarketReplayAuditResult(target_date=str(target_date))
        if self._is_paper_mode():
            result.skipped = True
            result.skip_reason = "historical_intraday_unavailable_in_paper"
            self._logger.info(f"post-market replay audit skipped: {result.skip_reason}")
            return result

        inputs = self._collect_audit_inputs(str(target_date))
        if not inputs:
            result.skipped = True
            result.skip_reason = "no_live_candidates"
            return result

        backtest_clock = BacktestMarketClock.from_clock(self._market_clock)
        replay_sqs = StockQueryBacktestReplayService(
            self._sqs,
            program_provider=self._program_provider,
            market_clock=backtest_clock,
        )
        replay_sqs.set_backtest_date(str(target_date))

        with tempfile.TemporaryDirectory(prefix="post_market_replay_audit_") as tmp_dir:
            for strategy_name, audit_input in sorted(inputs.items()):
                if not audit_input.candidates or not audit_input.scan_times:
                    continue
                strategy_result = await self._run_strategy_audit(
                    strategy_name=strategy_name,
                    audit_input=audit_input,
                    target_date=str(target_date),
                    replay_sqs=replay_sqs,
                    backtest_clock=backtest_clock,
                    state_dir=tmp_dir,
                )
                result.strategy_count += 1
                result.saved_runs.extend(strategy_result.saved_runs)
                result.missed_count += strategy_result.missed_count
                result.late_count += strategy_result.late_count
                result.missing_from_universe_count += strategy_result.missing_from_universe_count
                result.replayed_rejected_count += strategy_result.replayed_rejected_count
                result.data_unavailable_count += strategy_result.data_unavailable_count
        return result

    async def _run_strategy_audit(
        self,
        *,
        strategy_name: str,
        audit_input: _AuditInput,
        target_date: str,
        replay_sqs: StockQueryBacktestReplayService,
        backtest_clock: BacktestMarketClock,
        state_dir: str,
    ) -> PostMarketReplayAuditResult:
        result = PostMarketReplayAuditResult(target_date=target_date)
        signal_by_code: dict[str, dict] = {}
        rejected_by_code: dict[str, dict] = {}
        missing_by_code: dict[str, dict] = {}
        data_unavailable_by_code: dict[str, dict] = {}

        for scan_time in sorted(audit_input.scan_times):
            try:
                backtest_clock.set_backtest_datetime(_parse_signal_time(scan_time))
                replay_sqs.set_backtest_date(target_date)
                strategy = self._strategy_factory(
                    strategy_name=strategy_name,
                    replay_sqs=replay_sqs,
                    universe_service=self._universe_service,
                    indicator_service=self._indicator_service,
                    backtest_clock=backtest_clock,
                    state_dir=state_dir,
                    logger=self._debug_logger,
                )
                report = await StrategyDebugRunner(
                    strategy,
                    self._debug_logger,
                    target_date=target_date,
                    target_signal_time=_canonical_signal_time(scan_time),
                ).run(candidate_codes=sorted(audit_input.candidates))
            except Exception as exc:
                self._logger.warning(
                    "post-market replay audit scan failed: strategy=%s time=%s error=%s",
                    strategy_name,
                    scan_time,
                    exc,
                    exc_info=True,
                )
                for code in audit_input.candidates:
                    data_unavailable_by_code.setdefault(
                        code,
                        self._audit_record(
                            strategy=strategy_name,
                            code=code,
                            signal_time=scan_time,
                            status="data_unavailable",
                            rejected_reason=f"data_unavailable:{exc}",
                        ),
                    )
                continue

            for record in report.journal_records:
                code = str(record.get("code") or "")
                if not code:
                    continue
                audit_status = self._classify_record(record, audit_input)
                annotated = self._with_audit_metadata(
                    record,
                    audit_status=audit_status,
                    live_signal_time=audit_input.live_buy_times.get(code, ""),
                )
                if audit_status in {"missed_by_scheduler", "late_signal", "replay_matched"}:
                    signal_by_code.setdefault(code, annotated)
                elif audit_status == "missing_from_universe":
                    missing_by_code.setdefault(code, annotated)
                elif audit_status == "data_unavailable":
                    data_unavailable_by_code.setdefault(code, annotated)
                else:
                    rejected_by_code[code] = annotated

        records: list[dict] = []
        records.extend(signal_by_code.values())
        for code, record in missing_by_code.items():
            if code not in signal_by_code:
                records.append(record)
        for code, record in data_unavailable_by_code.items():
            if code not in signal_by_code and code not in missing_by_code:
                records.append(record)
        for code, record in rejected_by_code.items():
            if code not in signal_by_code and code not in missing_by_code and code not in data_unavailable_by_code:
                records.append(record)

        counts = Counter((record.get("metadata") or {}).get("audit_status") for record in records)
        result.missed_count = counts.get("missed_by_scheduler", 0)
        result.late_count = counts.get("late_signal", 0)
        result.missing_from_universe_count = counts.get("missing_from_universe", 0)
        result.replayed_rejected_count = counts.get("replayed_rejected", 0)
        result.data_unavailable_count = counts.get("data_unavailable", 0)

        saved = self._repo.save_run(
            records,
            run_id=f"audit_{strategy_name}_{target_date}",
            strategy=strategy_name,
            target_date=target_date,
            metadata={
                "audit_type": "missed_signal",
                "candidate_count": len(audit_input.candidates),
                "scan_time_count": len(audit_input.scan_times),
                "missed_count": result.missed_count,
                "late_count": result.late_count,
                "missing_from_universe_count": result.missing_from_universe_count,
                "replayed_rejected_count": result.replayed_rejected_count,
                "data_unavailable_count": result.data_unavailable_count,
            },
        )
        result.saved_runs.append(saved)
        return result

    def _collect_audit_inputs(self, target_date: str) -> dict[str, _AuditInput]:
        inputs = self._collect_inputs_from_logs(target_date)
        self._merge_scheduler_signals(inputs, target_date)
        self._merge_virtual_trades(inputs, target_date)
        return {name: item for name, item in inputs.items() if item.candidates}

    def _collect_inputs_from_logs(self, target_date: str) -> dict[str, _AuditInput]:
        date_prefix = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
        inputs: dict[str, _AuditInput] = {}
        for path in glob.glob(os.path.join(self._log_dir, "**", "*.log.json*"), recursive=True):
            strategy_name = _strategy_name_from_path(path)
            if not strategy_name:
                continue
            audit_input = inputs.setdefault(strategy_name, _AuditInput())
            open_fn = gzip.open if path.endswith(".gz") else open
            try:
                with open_fn(path, "rb") as fp:
                    for raw in fp:
                        try:
                            entry = json.loads(raw.decode("utf-8").strip())
                        except Exception:
                            continue
                        ts = str(entry.get("timestamp") or "")
                        if not ts.startswith(date_prefix):
                            continue
                        data = entry.get("data")
                        if not isinstance(data, dict):
                            continue
                        event = str(data.get("event") or "")
                        if event == "scan_with_watchlist":
                            audit_input.scan_times.add(_canonical_signal_time(ts))
                        code = str(data.get("code") or "").strip()
                        if code:
                            audit_input.candidates.add(code)
                            if event == "buy_signal_generated":
                                audit_input.live_buy_times.setdefault(code, _canonical_signal_time(ts))
            except OSError:
                continue
        return inputs

    def _merge_scheduler_signals(self, inputs: dict[str, _AuditInput], target_date: str) -> None:
        loader = getattr(self._scheduler_store, "load_signal_history_for_date", None)
        if not callable(loader):
            return
        try:
            rows = loader(target_date)
        except Exception:
            return
        for row in rows or []:
            strategy_name = str(row.get("strategy_name") or "").strip()
            code = str(row.get("code") or "").strip()
            if not strategy_name or not code:
                continue
            audit_input = inputs.setdefault(strategy_name, _AuditInput())
            audit_input.candidates.add(code)
            if str(row.get("action") or "").upper() == "BUY" and bool(row.get("api_success", True)):
                audit_input.live_buy_times.setdefault(code, _canonical_signal_time(str(row.get("timestamp") or "")))

    def _merge_virtual_trades(self, inputs: dict[str, _AuditInput], target_date: str) -> None:
        if self._virtual_trade_service is None:
            return
        getter = getattr(self._virtual_trade_service, "get_all_trades", None)
        if not callable(getter):
            return
        date_prefix = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
        try:
            trades = getter()
        except Exception:
            return
        for trade in trades or []:
            buy_date = str(trade.get("buy_date") or "")
            if not buy_date.startswith(date_prefix):
                continue
            strategy_name = str(trade.get("strategy") or "").strip()
            code = str(trade.get("code") or "").strip()
            if not strategy_name or not code:
                continue
            audit_input = inputs.setdefault(strategy_name, _AuditInput())
            audit_input.candidates.add(code)
            audit_input.live_buy_times.setdefault(code, _canonical_signal_time(buy_date))

    def _classify_record(self, record: dict, audit_input: _AuditInput) -> str:
        code = str(record.get("code") or "")
        status = str(record.get("status") or "").upper()
        rejected_reason = str(record.get("rejected_reason") or "")
        if rejected_reason.startswith("data_unavailable"):
            return "data_unavailable"
        if rejected_reason == "missing_from_universe":
            return "missing_from_universe"
        if status == "SIGNAL":
            live_time = audit_input.live_buy_times.get(code, "")
            if not live_time:
                return "missed_by_scheduler"
            if _parse_signal_time(live_time) > _parse_signal_time(str(record.get("signal_time") or "")):
                return "late_signal"
            return "replay_matched"
        return "replayed_rejected"

    @staticmethod
    def _with_audit_metadata(record: dict, *, audit_status: str, live_signal_time: str) -> dict:
        copied = dict(record)
        metadata = dict(copied.get("metadata") or {})
        metadata["audit_status"] = audit_status
        metadata["live_signal_time"] = live_signal_time
        copied["metadata"] = metadata
        return copied

    @staticmethod
    def _audit_record(
        *,
        strategy: str,
        code: str,
        signal_time: str,
        status: str,
        rejected_reason: str,
    ) -> dict:
        record = normalize_backtest_decision(
            {
                "signal_time": _canonical_signal_time(signal_time),
                "rejected_reason": rejected_reason,
                "strategy": strategy,
            },
            stock_code=code,
            strategy=strategy,
            accepted=False,
        )
        metadata = dict(record.get("metadata") or {})
        metadata["audit_status"] = status
        record["metadata"] = metadata
        return record

    def _default_strategy_factory(self, **kwargs):
        from scripts.run_backtest import _build_backtest_strategy

        strategy_name = kwargs["strategy_name"]
        strategy_key = _STRATEGY_KEY_BY_NAME.get(strategy_name)
        if not strategy_key:
            raise ValueError(f"unsupported audit strategy: {strategy_name}")
        return _build_backtest_strategy(
            strategy_key=strategy_key,
            replay_sqs=kwargs["replay_sqs"],
            universe_service=kwargs["universe_service"],
            indicator_service=kwargs.get("indicator_service"),
            backtest_clock=kwargs["backtest_clock"],
            state_dir=kwargs["state_dir"],
            logger=kwargs.get("logger"),
        )

    def _is_paper_mode(self) -> bool:
        return bool(getattr(self._env, "is_paper_trading", False))


def _strategy_name_from_path(path: str) -> str | None:
    match = _STRATEGY_NAME_RE.match(os.path.basename(path))
    return match.group(1) if match else None


def _canonical_signal_time(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 19 and text[4] == "-" and text[7] == "-":
        return text[:19]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 14:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} 00:00:00"
    return text


def _parse_signal_time(value: str) -> datetime:
    canonical = _canonical_signal_time(value)
    try:
        return datetime.strptime(canonical, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.min
