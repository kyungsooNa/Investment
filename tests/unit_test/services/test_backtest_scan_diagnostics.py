import json
import logging
from unittest.mock import AsyncMock

import pytest

from repositories.backtest_journal_repository import BacktestJournalRepository
from services.backtest_execution_simulator import BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner
from services.validation_campaign_service import ValidationCampaignService
from scripts.run_backtest import _result_to_payload
from strategies.debug.rejection_collector import RejectionCollector
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.first_pullback_strategy import FirstPullbackStrategy


class DiagnosticStrategy:
    name = "diagnostic"

    def __init__(self):
        self._logger = logging.getLogger("test.period.diagnostics")
        self._logger.setLevel(logging.INFO)

    async def check_exits(self, holdings):
        return []

    async def scan(self):
        self._logger.info({"event": "scan_with_watchlist", "count": 4})
        for code in ("A", "B", "C"):
            self._logger.debug({"event": "candidate_evaluated", "code": code})
        for event in ("breakout_rejected", "entry_rejected"):
            self._logger.debug({"event": event, "code": "A", "reason": "not_in_squeeze"})
        self._logger.info({"event": "entry_rejected", "code": "B", "reason": "price_unavailable"})
        return []


@pytest.mark.asyncio
async def test_zero_trade_diagnostics_persist_without_contaminating_journal(tmp_path):
    strategy = DiagnosticStrategy()
    handlers = list(strategy._logger.handlers)
    runner = BacktestPeriodRunner(
        strategy=strategy, bar_provider=None,
        ledger=BacktestPortfolioLedger(initial_cash=1000000),
        backtest_journal_repository=BacktestJournalRepository(tmp_path),
    )
    result = await runner.run(["20261001", "20261002"])
    assert result.journal_records == []
    assert strategy._logger.level == logging.INFO
    assert strategy._logger.handlers == handlers
    rows = result.scan_diagnostics
    assert [row["date"] for row in rows] == ["20261001", "20261002"]
    assert rows[0]["evaluated_candidates"] == 3
    assert rows[0]["watchlist_candidates"] == 4
    assert rows[0]["reason_counts"] == {"not_in_squeeze": 1, "price_unavailable": 1}
    assert rows[0]["unexplained_codes"] == ["C"]
    assert rows[0]["buy_signals"] == 0
    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert stored["metadata"]["scan_diagnostics"] == rows
    payload = _result_to_payload(result)
    assert payload["scan_diagnostics"] == rows
    summary = ValidationCampaignService._summarize("diagnostic", payload, candidate_observations=6)
    assert summary["scan_diagnostics"] == rows
    assert summary["rejected_records"] == 0
    assert summary["zero_trade_reason"] == "no_signal_observed"


@pytest.mark.asyncio
async def test_scan_skipped_is_not_misreported_as_candidate_rejection():
    strategy = DiagnosticStrategy()

    async def skip():
        strategy._logger.info({"event": "scan_skipped", "reason": "Before entry cutoff (15:10)"})
        return []

    strategy.scan = skip
    runner = BacktestPeriodRunner(strategy=strategy, bar_provider=None,
                                  ledger=BacktestPortfolioLedger(initial_cash=1000000))
    row = (await runner.run(["20261001"])).scan_diagnostics[0]
    assert row["evaluated_candidates"] == 0
    assert row["watchlist_candidates"] is None
    assert row["reason_counts"] == {}
    assert row["scan_skip_reasons"] == {"Before entry cutoff (15:10)": 1}


@pytest.mark.asyncio
async def test_scan_exception_restores_logger():
    strategy = DiagnosticStrategy()
    handlers = list(strategy._logger.handlers)

    async def fail():
        raise RuntimeError("scan failed")

    strategy.scan = fail
    runner = BacktestPeriodRunner(strategy=strategy, bar_provider=None,
                                  ledger=BacktestPortfolioLedger(initial_cash=1000000))
    with pytest.raises(RuntimeError, match="scan failed"):
        await runner.run(["20261001"])
    assert strategy._logger.level == logging.INFO
    assert strategy._logger.handlers == handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_type,method", [
    (OneilPocketPivotStrategy, "_check_entry"),
    (OneilSqueezeBreakoutStrategy, "_check_breakout"),
    (FirstPullbackStrategy, "_check_entry"),
])
async def test_missing_price_is_reported_as_data_gap(strategy_type, method):
    strategy = object.__new__(strategy_type)
    strategy._logger = logging.getLogger("test.period.missing_price")
    strategy._sqs = AsyncMock()
    strategy._sqs.get_current_price.return_value = None
    with RejectionCollector(logger=strategy._logger) as collector:
        assert await getattr(strategy, method)("005930", None, 0.5) is None
    assert [(event.code, event.reason) for event in collector.events] == [("005930", "price_unavailable")]
