from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse
from strategies.oneil_common_types import OSBWatchlistItem
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "backtest"
    / "oneil_pp_bgu_entry_cases.json"
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
    tm.get_market_open_time.return_value = datetime.combine(now.date(), datetime.strptime("09:00:00", "%H:%M:%S").time())
    tm.get_market_close_time.return_value = datetime.combine(now.date(), datetime.strptime("15:30:00", "%H:%M:%S").time())
    return tm


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
async def test_oneil_pp_bgu_entry_fixture_cases(case, tmp_path, monkeypatch):
    monkeypatch.setattr(OneilPocketPivotStrategy, "_load_state", lambda self: None)

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

    strategy = OneilPocketPivotStrategy(
        stock_query_service=sqs,
        universe_service=universe,
        market_clock=_market_clock(case),
        logger=MagicMock(),
        state_file=str(tmp_path / f"{case['id']}.json"),
    )

    signals = await strategy.scan()

    expected = case["expected"]
    if expected["signal"]:
        assert len(signals) == 1
        assert signals[0].code == "005930"
        assert signals[0].action == "BUY"
        assert expected["reason_contains"] in signals[0].reason
        assert strategy._position_state["005930"].entry_type == expected["entry_type"]
    else:
        assert signals == []
        assert "005930" not in strategy._position_state

    if not case["market_timing_ok"]:
        sqs.get_current_price.assert_not_called()
