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
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.oneil_common_types import HTFConfig, OSBWatchlistItem


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "high_tight_flag_entry_cases.json"
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


def _make_htf_ohlcv() -> list[dict]:
    rows: list[dict] = []
    for i in range(40):
        rows.append({
            "date": f"202603{i + 1:02d}",
            "open": 50_000,
            "high": 55_000,
            "low": 50_000,
            "close": 52_000,
            "volume": 180_000,
        })
    for i in range(15):
        price = 55_000 + i * 3_000
        rows.append({
            "date": f"202604{i + 1:02d}",
            "open": price - 1_000,
            "high": price + 500,
            "low": price - 2_000,
            "close": price,
            "volume": 220_000,
        })
    rows[-1]["high"] = 100_000
    rows[-1]["close"] = 99_500

    for i in range(10):
        rows.append({
            "date": f"202605{i + 1:02d}",
            "open": 95_000,
            "high": 97_000,
            "low": 91_000,
            "close": 94_000 + (i % 3) * 500,
            "volume": 90_000,
        })
    return rows


def _watchlist_item(case: dict) -> OSBWatchlistItem:
    candidate = case["candidate"]
    return OSBWatchlistItem(
        code="005930",
        name="삼성전자",
        market="KOSPI",
        high_20d=100000,
        ma_20d=95000,
        ma_50d=90000,
        avg_vol_20d=100000,
        bb_width_min_20d=1000,
        prev_bb_width=1100,
        w52_hgpr=105000,
        avg_trading_value_5d=float(candidate["avg_trading_value_5d"]),
        market_cap=int(candidate["market_cap"]),
    )


def _price_response(price: dict) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"output": {
            "stck_prpr": str(price["current"]),
            "stck_hgpr": str(price["day_high"]),
            "stck_lwpr": str(price["day_low"]),
            "acml_vol": str(price["volume"]),
            "pgtr_ntby_qty": str(price["program_buy_qty"]),
            "acml_tr_pbmn": str(price["trade_value"]),
        }},
    )


def _ohlcv_response() -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data=_make_htf_ohlcv(),
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
    logger = logging.getLogger(f"htf_fixture_parity_{case_id}_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _strategy_for_case(case: dict, logger: logging.Logger, tmp_path: Path):
    sqs = MagicMock()
    sqs.get_current_price = AsyncMock(return_value=_price_response(case["price"]))
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv_response())
    sqs.get_stock_conclusion = AsyncMock(
        return_value=_conclusion_response(case["execution_strength"])
    )

    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": _watchlist_item(case)})
    universe.is_market_timing_ok = AsyncMock(return_value=True)

    strategy = HighTightFlagStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        market_clock=_market_clock(case),
        config=HTFConfig(
            pole_min_surge_ratio=1.9,
            breakout_min_buffer_pct=0.5,
            breakout_max_extension_pct=2.0,
            min_candle_relative_pos=0.7,
            volume_breakout_multiplier=2.0,
            execution_strength_min=120.0,
            program_to_market_cap_pct=0.3,
            stop_loss_pct=-5.0,
        ),
        logger=logger,
        state_file=str(tmp_path / f"htf_state_{case['id']}.json"),
    )
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_htf_fixture_results_match_period_runner_and_debug_runner(case, tmp_path):
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
