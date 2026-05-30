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
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "oneil_squeeze_breakout_entry_cases.json"
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
            timestamp=f"{date_ymd} 120000",
            open=signal.price,
            high=signal.price + 500,
            low=signal.price - 500,
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
        high_20d=int(case["high_20d"]),
        ma_20d=68000,
        ma_50d=65000,
        avg_vol_20d=int(case["avg_vol_20d"]),
        bb_width_min_20d=float(case["bb_width_min_20d"]),
        prev_bb_width=float(case["prev_bb_width"]),
        w52_hgpr=80000,
        avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
        source=case["source"],
    )


def _price_response(price: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={
            "output": {
                "stck_prpr": str(price["current"]),
                "stck_hgpr": str(price["high"]),
                "stck_lwpr": str(price["low"]),
                "acml_vol": str(price["volume"]),
                "pgtr_ntby_qty": str(price["program_buy_qty"]),
                "acml_tr_pbmn": str(price["trade_value"]),
            }
        },
    )


def _conclusion_response(execution_strength: float) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": [{"tday_rltv": str(execution_strength)}]},
    )


def _market_clock(case: dict):
    now = datetime.strptime(f"{case['date']} {case['time']}", "%Y%m%d %H:%M:%S")
    tm = MagicMock()
    tm.get_current_kst_time.return_value = now
    tm.get_market_open_time.return_value = datetime.combine(
        now.date(),
        datetime.strptime("09:00:00", "%H:%M:%S").time(),
    )
    tm.get_market_close_time.return_value = datetime.combine(
        now.date(),
        datetime.strptime("15:30:00", "%H:%M:%S").time(),
    )
    return tm


def _debug_logger(case_id: str) -> logging.Logger:
    logger = logging.getLogger(f"osb_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, tmp_path, logger: logging.Logger):
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.prefetch_prices = AsyncMock(return_value=0)
    sqs.get_stock_conclusion = AsyncMock(
        return_value=_conclusion_response(case["execution_strength"])
    )
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[]))

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item(case)})
    universe.is_market_timing_ok = AsyncMock(return_value=case["market_timing_ok"])

    strategy = OneilSqueezeBreakoutStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        market_clock=_market_clock(case),
        logger=logger,
        state_file=str(tmp_path / f"{case['id']}_{id(logger)}.json"),
    )
    strategy._save_state = MagicMock()
    return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_osb_fixture_results_match_period_runner_and_debug_runner(case, tmp_path):
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
    else:
        assert period_result.execution_reports == []
        assert any(
            record["status"] == "REJECTED"
            and case["expected"]["rejected_reason"] in str(record.get("rejected_reason", ""))
            for record in debug_report.journal_records
        )
