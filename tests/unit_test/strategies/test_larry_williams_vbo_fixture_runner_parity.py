from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.backtest_execution_simulator import BacktestBar, BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from strategies.larry_williams_vbo_strategy import (
    LarryWilliamsVBOConfig,
    LarryWilliamsVBOStrategy,
)
from strategies.oneil_common_types import OSBWatchlistItem


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "larry_williams_vbo_entry_cases.json"
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
            timestamp=f"{date_ymd} 100000",
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
    candidate = case["candidate"]
    return OSBWatchlistItem(
        code="005930",
        name="삼성전자",
        market="KOSPI",
        high_20d=70000,
        ma_20d=68000,
        ma_50d=65000,
        avg_vol_20d=100000,
        bb_width_min_20d=1000,
        prev_bb_width=1100,
        w52_hgpr=80000,
        avg_trading_value_5d=float(candidate["avg_trading_value_5d"]),
        market_cap=int(candidate["market_cap"]),
    )


def _price_response(price: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "price": str(price["current"]),
            "stck_prpr": str(price["current"]),
            "open": str(price["open"]),
            "pgtr_ntby_qty": str(price["program_buy_qty"]),
            "acml_tr_pbmn": str(price["trade_value"]),
        },
    )


def _ohlcv_response(case: dict) -> ResCommonResponse:
    range_data = case["range"]
    row = {
        "date": "20260512",
        "open": int(range_data["low"]) + 100,
        "high": int(range_data["high"]),
        "low": int(range_data["low"]),
        "close": (int(range_data["high"]) + int(range_data["low"])) // 2,
        "volume": 10_000_000,
    }
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[row, row])


def _conclusion_response(execution_strength: float) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": [{"tday_rltv": str(execution_strength)}]},
    )


def _market_clock(case: dict):
    now = datetime.strptime(f"{case['date']} {case['time']}", "%Y%m%d %H:%M:%S")
    tm = MagicMock()
    tm.get_current_kst_time.return_value = now
    return tm


def _debug_logger(case_id: str) -> logging.Logger:
    logger = logging.getLogger(f"vbo_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, logger: logging.Logger):
    sqs = MagicMock()
    sqs.handle_get_current_stock_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv_response(case))
    sqs.get_stock_conclusion = AsyncMock(
        return_value=_conclusion_response(case["execution_strength"])
    )

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item(case)})

    return LarryWilliamsVBOStrategy(
        stock_query_service=sqs,
        market_clock=_market_clock(case),
        universe_service=universe,
        config=LarryWilliamsVBOConfig(
            k_value=0.5,
            min_market_cap=200_000_000_000,
            min_5d_trading_value=10_000_000_000,
            confidence_threshold=120.0,
            program_buy_ratio=0.10,
            stop_loss_pct=-3.0,
        ),
        logger=logger,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_vbo_fixture_results_match_period_runner_and_debug_runner(case):
    expected_signal = bool(case["expected"]["signal"])

    period_strategy = _strategy_for_case(case, _debug_logger(f"{case['id']}_period"))
    period_result = await BacktestPeriodRunner(
        strategy=period_strategy,
        bar_provider=FixtureBarProvider(),
        ledger=BacktestPortfolioLedger(initial_cash=10_000_000),
    ).run([case["date"]])

    debug_strategy = _strategy_for_case(case, _debug_logger(f"{case['id']}_debug"))
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
