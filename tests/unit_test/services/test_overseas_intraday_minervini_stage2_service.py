from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from services.overseas_intraday_minervini_stage2_service import (
    OverseasIntradayMinerviniStage2Service,
)


NY = pytz.timezone("America/New_York")
TRADE_DATE = "20261016"


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


def _rows(count=220):
    return [
        {
            "date": f"{20250000 + i:08d}",
            "open": 90.0,
            "high": 100.0,
            "low": 89.0,
            "close": 95.0,
            "volume": 1_000_000,
        }
        for i in range(count)
    ]


def _build(*, stages=None):
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD"},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ok(_rows()))
    orders = MagicMock()
    orders.place_entry = AsyncMock(return_value=_ok({"would_be": True}))
    orders.place_exit = AsyncMock(return_value=_ok({"would_be": True}))
    stage_service = MagicMock()
    stage_service.classify_stage_series.return_value = stages or ([1] * 212 + [2] * 8)
    clock = MagicMock()
    clock.get_current_kst_time.return_value = NY.localize(datetime(2026, 10, 16, 12, 0))

    service = OverseasIntradayMinerviniStage2Service(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        order_execution_service=orders,
        minervini_stage_service=stage_service,
        market_clock=clock,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, orders=orders, stage_service=stage_service)


@pytest.mark.asyncio
async def test_enters_recent_stage2_transition_on_20day_breakout_with_dollar_liquidity():
    built = _build()
    assert await built.service.prepare_session(TRADE_DATE) == 1

    action = await built.service.on_price("AAA", 101.0, volume=250_000)

    assert action["action"] == "BUY"
    assert action["reason"] == "minervini_stage2_transition_intraday_breakout"
    assert action["pivot"] == 100.0
    assert action["intraday_trading_value_usd"] == 25_250_000.0


@pytest.mark.asyncio
async def test_rejects_stage2_without_recent_transition():
    built = _build(stages=[2] * 220)

    assert await built.service.prepare_session(TRADE_DATE) == 0


@pytest.mark.asyncio
async def test_rejects_overextended_or_illiquid_breakout():
    built = _build()
    await built.service.prepare_session(TRADE_DATE)

    assert await built.service.on_price("AAA", 106.0, volume=1_000_000) is None
    assert await built.service.on_price("AAA", 101.0, volume=100_000) is None
    built.orders.place_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_exits_at_four_percent_target():
    built = _build()
    await built.service.prepare_session(TRADE_DATE)
    await built.service.on_price("AAA", 101.0, volume=250_000)

    action = await built.service.on_price("AAA", 105.04, volume=300_000)

    assert action["action"] == "SELL"
    assert action["exit_reason"] == "target"
    assert action["realized_pct"] == pytest.approx(4.0)


def test_history_limit_covers_stage_transition_window():
    assert OverseasIntradayMinerviniStage2Service.HISTORY_LIMIT >= 210
