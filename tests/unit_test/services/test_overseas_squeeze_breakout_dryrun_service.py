"""해외 O'Neil Squeeze Breakout dry-run 신호 서비스 테스트."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_squeeze_breakout_dryrun_service import OverseasSqueezeBreakoutDryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _osb_bars(*, current_close=122.0, current_volume=220_000, current_low=118.0):
    bars = []
    for i in range(20):
        close = 100.0 + i * 0.6
        bars.append(_bar(f"202604{i + 1:02d}", close, 120.0, close - 1.0, close, 100_000))
    for i in range(20):
        close = 118.0 + (0.02 if i % 2 == 0 else -0.02)
        bars.append(_bar(f"202605{i + 1:02d}", close, 120.0, close - 1.0, close, 100_000))
    bars.append(_bar("20260610", 119.0, 123.0, current_low, current_close, current_volume))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "NVDA", "name": "Nvidia", "exchange": "NASD", "avg_trading_value": 500_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()
    service = OverseasSqueezeBreakoutDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(
        service=service,
        candidate_service=candidate_service,
        sqs=sqs,
        journal=journal,
    )


@pytest.mark.asyncio
async def test_scan_emits_buy_on_squeeze_breakout(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "O'NeilOSB_overseas"
    assert sig["code"] == "NVDA"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_squeeze_breakout"
    assert sig["entry_price"] == 122.0
    assert sig["breakout_level"] == 120.0
    assert sig["volume_ratio"] == pytest.approx(2.2)
    assert sig["bb_width"] <= sig["bb_width_min_20d"] * 1.2
    assert sig["stop_price"] > 0


@pytest.mark.asyncio
async def test_scan_skips_without_breakout(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars(current_close=119.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_volume_is_low(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars(current_volume=120_000)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_candle_closes_poorly(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(
        return_value=_ohlcv(_osb_bars(current_close=120.5, current_low=118.0))
    )

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_osb_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "NVDA"
    assert kwargs["strategy_name"] == "O'NeilOSB_overseas"
    assert kwargs["signal_source"] == "overseas_osb_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NYSE"
    assert kwargs["snapshot"]["bar"]["date"] == "20260610"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "NVDA", "name": "Nvidia", "exchange": "NASD", "avg_trading_value": 500_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 3, "notional_usd": 366.0, "reason": "slot"})

    service = OverseasSqueezeBreakoutDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 3
    assert signals[0]["notional_usd"] == 366.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")


@pytest.mark.asyncio
async def test_scan_copies_fx_fields_from_sizing_result(svc):
    """사이징이 환율/원화 노출을 돌려주면 신호에 그대로 옮겨 담는다."""
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={
        "qty": 2,
        "notional_usd": 200.0,
        "fx_krw_per_usd": 1380.0,
        "krw_exposure": 276_000.0,
    })
    svc.service._sizing_service = sizing
    svc.service._fx_provider = AsyncMock(return_value=1380.0)

    signals = await svc.service.scan_dry_run()

    assert signals[0]["qty"] == 2
    assert signals[0]["fx_krw_per_usd"] == 1380.0
    assert signals[0]["krw_exposure"] == 276_000.0
    assert sizing.size.call_args.kwargs["fx_krw_per_usd"] == 1380.0


@pytest.mark.asyncio
async def test_scan_omits_fx_fields_when_sizing_returns_none(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={
        "qty": 2,
        "notional_usd": 200.0,
        "fx_krw_per_usd": None,
        "krw_exposure": None,
    })
    svc.service._sizing_service = sizing

    signals = await svc.service.scan_dry_run()

    assert "fx_krw_per_usd" not in signals[0]
    assert "krw_exposure" not in signals[0]


@pytest.mark.asyncio
async def test_scan_can_skip_shadow_journal_recording(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_osb_bars()))

    signals = await svc.service.scan_dry_run(record=False)

    assert len(signals) == 1
    svc.journal.record.assert_not_called()
