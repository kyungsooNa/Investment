"""해외 Larry Williams Channel Breakout dry-run 신호 서비스 테스트."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_channel_breakout_dryrun_service import OverseasChannelBreakoutDryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _cb_bars(*, current_close=125.0, current_volume=200_000):
    bars = []
    for i in range(35):
        close = 100.0 + i * 0.2
        bars.append(_bar(f"202604{i + 1:02d}", close, 119.0, 95.0 + i * 0.1, close, 100_000))
    bars.append(_bar("20260506", 121.0, 126.0, 120.0, current_close, current_volume))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value={"adx": 30.0, "adx_rising": True})
    journal = MagicMock()
    service = OverseasChannelBreakoutDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        indicator_service=indicator,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(
        service=service,
        candidate_service=candidate_service,
        sqs=sqs,
        indicator=indicator,
        journal=journal,
    )


@pytest.mark.asyncio
async def test_scan_emits_buy_on_channel_breakout(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "LarryWilliamsCB_overseas"
    assert sig["code"] == "MSFT"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_channel_breakout"
    assert sig["entry_price"] == 125.0
    assert sig["channel_high"] == 119.0
    assert sig["volume_ratio"] == 2.0
    assert sig["adx"] == 30.0
    assert sig["stop_price"] > 0


@pytest.mark.asyncio
async def test_scan_skips_without_channel_breakout(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars(current_close=118.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_volume_is_low(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars(current_volume=120_000)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_adx_filter_fails(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars()))
    svc.indicator.calc_adx_sync.return_value = {"adx": 20.0, "adx_rising": True}

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_cb_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "MSFT"
    assert kwargs["strategy_name"] == "LarryWilliamsCB_overseas"
    assert kwargs["signal_source"] == "overseas_cb_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NYSE"
    assert kwargs["snapshot"]["bar"]["date"] == "20260506"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_cb_bars()))
    indicator = MagicMock()
    indicator.calc_adx_sync = MagicMock(return_value={"adx": 30.0, "adx_rising": True})
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 4, "notional_usd": 500.0, "reason": "slot"})

    service = OverseasChannelBreakoutDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        indicator_service=indicator,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 4
    assert signals[0]["notional_usd"] == 500.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")
