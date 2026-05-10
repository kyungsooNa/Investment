from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse
from services.backtest_execution_simulator import BacktestBar, BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from strategies.oneil_common_types import OSBWatchlistItem
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "oneil_pp_bgu_entry_cases.json"
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


def _watchlist_item() -> OSBWatchlistItem:
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
        avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
    )


def _price_response(price: dict) -> ResCommonResponse:
    current = int(price["current"])
    prev_close = int(price["prev_close"])
    prdy_vrss = abs(current - prev_close)
    prdy_vrss_sign = "2" if current > prev_close else ("5" if current < prev_close else "3")
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={
            "output": {
                "stck_prpr": str(current),
                "acml_vol": str(price["volume"]),
                "pgtr_ntby_qty": str(price["program_buy_qty"]),
                "acml_tr_pbmn": str(price["trade_value"]),
                "stck_oprc": str(price["open"]),
                "stck_hgpr": str(price["high"]),
                "stck_lwpr": str(price["low"]),
                "prdy_vrss": str(prdy_vrss),
                "prdy_vrss_sign": prdy_vrss_sign,
            }
        },
    )


def _conclusion_response(execution_strength: float) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": [{"tday_rltv": str(execution_strength)}]},
    )


def _ohlcv_for_case(case: dict) -> list[dict]:
    base_date = datetime.strptime(case["date"], "%Y%m%d").date()
    rows: list[dict] = []
    for offset in range(60, 0, -1):
        day = base_date - timedelta(days=offset)
        if case["ohlcv_profile"] == "bgu_up_days":
            open_price = 68500
            close_price = 69000
            volume = 100000
        else:
            open_price = 68000
            close_price = 67500
            volume = 50000
        rows.append(
            {
                "date": day.strftime("%Y%m%d"),
                "open": open_price,
                "close": close_price,
                "high": max(open_price, close_price) + 500,
                "low": min(open_price, close_price) - 500,
                "volume": volume,
            }
        )
    return rows


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
    logger = logging.getLogger(f"oneil_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, tmp_path, logger: logging.Logger):
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=_ohlcv_for_case(case))
    )
    sqs.get_stock_conclusion = AsyncMock(
        return_value=_conclusion_response(case["execution_strength"])
    )

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item()})
    universe.is_market_timing_ok = AsyncMock(return_value=case["market_timing_ok"])

    return OneilPocketPivotStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        market_clock=_market_clock(case),
        logger=logger,
        state_file=str(tmp_path / f"{case['id']}_{id(logger)}.json"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_oneil_fixture_results_match_period_runner_and_debug_runner(case, tmp_path):
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
            for record in debug_report.journal_records
        )
