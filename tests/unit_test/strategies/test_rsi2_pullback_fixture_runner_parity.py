from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse
from services.backtest_execution_simulator import BacktestBar, BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from strategies.oneil_common_types import OSBWatchlistItem
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "rsi2_pullback_entry_cases.json"
)


class FixtureBarProvider:
    async def get_bar(
        self,
        *,
        signal,
        date_ymd: str,
        side: str,
        execution_policy: str = "current_bar",
    ) -> BacktestBar:
        return BacktestBar(
            timestamp=f"{date_ymd} 151000",
            open=signal.price,
            high=signal.price + 100,
            low=signal.price - 100,
            close=signal.price,
            volume=10_000,
        )


def _load_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


def _watchlist_item(case: dict) -> OSBWatchlistItem:
    return OSBWatchlistItem(
        code="005930",
        name="삼성전자",
        market="KOSPI",
        high_20d=12000,
        ma_20d=10500,
        ma_50d=10000,
        avg_vol_20d=100000,
        bb_width_min_20d=500,
        prev_bb_width=600,
        w52_hgpr=15000,
        avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
        ma_200d=9500.0,
        minervini_stage=int(case["minervini_stage"]),
    )


def _price_response(price: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": {"stck_prpr": str(price["current"])}},
    )


def _rsi_response(value: float) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data=[{"code": "005930", "date": "20260507", "close": 10000.0, "rsi": value}],
    )


def _market_clock(case: dict):
    now = datetime.strptime(f"{case['date']} {case['time']}", "%Y%m%d %H:%M:%S")
    tm = MagicMock()
    tm.get_current_kst_time.return_value = now
    return tm


def _debug_logger(case_id: str) -> logging.Logger:
    logger = logging.getLogger(f"rsi2_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, tmp_path, logger: logging.Logger):
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.prefetch_prices = AsyncMock(return_value=0)

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item(case)})
    universe.is_market_timing_ok = AsyncMock(return_value=case["market_timing_ok"])

    indicator = MagicMock()
    indicator.get_rsi = AsyncMock(return_value=_rsi_response(float(case["rsi"])))
    indicator.get_moving_average = AsyncMock()

    return RSI2PullbackStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        indicator_service=indicator,
        market_clock=_market_clock(case),
        logger=logger,
        state_file=str(tmp_path / f"{case['id']}_{id(logger)}.json"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_rsi2_fixture_results_match_period_runner_and_debug_runner(case, tmp_path):
    expected_signal = bool(case["expected"]["signal"])

    period_strategy = _strategy_for_case(case, tmp_path, _debug_logger(f"{case['id']}_period"))
    period_result = await BacktestPeriodRunner(
        strategy=period_strategy,
        bar_provider=FixtureBarProvider(),
        ledger=BacktestPortfolioLedger(initial_cash=10_000_000),
    ).run([case["date"]])

    debug_strategy = _strategy_for_case(case, tmp_path, _debug_logger(f"{case['id']}_debug"))
    debug_report = await StrategyDebugRunner(
        debug_strategy,
        debug_strategy._logger,
        target_date=case["date"],
    ).run(candidate_codes=["005930"])

    period_buy_filled = any(
        report.order.side.value == "BUY" and report.filled_qty > 0
        for report in period_result.execution_reports
    )
    debug_buy_signaled = any(signal.action == "BUY" for signal in debug_report.signals)

    assert period_buy_filled is expected_signal
    assert debug_buy_signaled is expected_signal

    if expected_signal:
        assert [record["status"] for record in debug_report.journal_records] == ["SIGNAL"]
        assert period_result.journal_records[0]["status"] == "FILLED"
        assert case["expected"]["reason_contains"] in debug_report.signals[0].reason
        assert debug_strategy._position_state["005930"].risk_off_entry is case["expected"]["risk_off"]
    else:
        assert period_result.execution_reports == []
        assert any(
            record["status"] == "REJECTED"
            and case["expected"]["rejected_reason"] in str(record.get("rejected_reason", ""))
            for record in debug_report.journal_records
        )
