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
from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.first_pullback_types import FirstPullbackConfig
from strategies.oneil_common_types import OSBWatchlistItem


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "first_pullback_entry_cases.json"
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


def _make_fp_ohlcv(variant: str) -> list[dict]:
    if variant == "declining":
        base = 15_000
        rows = []
        for i in range(30):
            if i == 15:
                close = int(base * 1.30)
                rows.append({
                    "date": f"202604{i + 1:02d}",
                    "open": base,
                    "close": close,
                    "high": close,
                    "low": base,
                    "volume": 200_000,
                })
                base = close
            else:
                close = int(base * 0.97)
                rows.append({
                    "date": f"202604{i + 1:02d}",
                    "open": base,
                    "close": close,
                    "high": base,
                    "low": close,
                    "volume": 30_000,
                })
                base = close
        return rows

    base = 10_000
    rows = []
    surge_type = "none" if variant == "none" else "upper_limit"
    for i in range(30):
        if surge_type == "upper_limit" and i == 15:
            close = int(base * 1.30)
            rows.append({
                "date": f"202604{i + 1:02d}",
                "open": base + 100,
                "close": close,
                "high": close + 200,
                "low": base,
                "volume": 200_000,
            })
            base = close
            continue

        close = int(base * 1.002)
        rows.append({
            "date": f"202604{i + 1:02d}",
            "open": base,
            "close": close,
            "high": close + 100,
            "low": base - 100,
            "volume": 30_000,
        })
        base = close

    if variant == "upper_limit_high_recent_volume":
        for row in rows[-3:]:
            row["volume"] = 150_000
    return rows


def _watchlist_item() -> OSBWatchlistItem:
    return OSBWatchlistItem(
        code="005930",
        name="삼성전자",
        market="KOSPI",
        high_20d=14000,
        ma_20d=12746,
        ma_50d=11000,
        avg_vol_20d=100000,
        bb_width_min_20d=500,
        prev_bb_width=600,
        w52_hgpr=15000,
        avg_trading_value_5d=50_000_000_000,
        market_cap=500_000_000_000,
    )


def _price_response(price: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": {
            "stck_prpr": str(price["current"]),
            "stck_oprc": str(price["today_open"]),
            "stck_hgpr": str(price["today_high"]),
            "stck_lwpr": str(price["today_low"]),
            "prdy_vrss": str(price["previous_diff"]),
            "prdy_vrss_sign": str(price["previous_diff_sign"]),
        }},
    )


def _ohlcv_response(case: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data=_make_fp_ohlcv(case["ohlcv_variant"]),
    )


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
    tm.get_market_open_time.return_value = now.replace(hour=9, minute=0, second=0)
    tm.get_market_close_time.return_value = now.replace(hour=15, minute=30, second=0)
    return tm


def _debug_logger(case_id: str) -> logging.Logger:
    logger = logging.getLogger(f"fp_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, logger: logging.Logger, tmp_path: Path):
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv_response(case))
    sqs.get_stock_conclusion = AsyncMock(
        return_value=_conclusion_response(case["execution_strength"])
    )

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item()})
    universe.is_market_timing_ok = AsyncMock(return_value=True)

    strategy = FirstPullbackStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        market_clock=_market_clock(case),
        config=FirstPullbackConfig(
            upper_limit_pct=29.0,
            rapid_surge_pct=30.0,
            ma_period=20,
            ma_rising_days=5,
            ma_rising_min_count=4,
            pullback_lower_pct=-1.0,
            pullback_upper_pct=3.0,
            volume_dryup_ratio=0.5,
            volume_dryup_days=3,
            execution_strength_min=100.0,
            reversal_prev_close_floor_pct=-2.0,
            reversal_min_relative_pos=0.5,
        ),
        logger=logger,
        state_file=str(tmp_path / f"fp_state_{case['id']}.json"),
    )
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_fp_fixture_results_match_period_runner_and_debug_runner(case, tmp_path):
    expected_signal = bool(case["expected"]["signal"])

    period_strategy = _strategy_for_case(
        case,
        _debug_logger(f"{case['id']}_period"),
        tmp_path,
    )
    period_result = await BacktestPeriodRunner(
        strategy=period_strategy,
        bar_provider=FixtureBarProvider(),
        ledger=BacktestPortfolioLedger(initial_cash=10_000_000),
    ).run([case["date"]])

    debug_strategy = _strategy_for_case(
        case,
        _debug_logger(f"{case['id']}_debug"),
        tmp_path,
    )
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
