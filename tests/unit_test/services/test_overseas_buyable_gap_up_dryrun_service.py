"""해외 Buyable Gap-Up dry-run 신호 서비스 테스트.

완성 일봉 기준으로 BGU 조건을 판정하고 shadow 저널에 would-be 신호만 기록한다.
해외 자동 주문 경로는 열지 않는다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_buyable_gap_up_dryrun_service import OverseasBuyableGapUpDryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _bgu_bars(*, current_open=105.0, current_close=107.0, current_volume=160_000):
    bars = [
        _bar(f"202604{i + 1:02d}", 99, 101, 98, 100, 10_000)
        for i in range(50)
    ]
    bars.append(_bar("20260521", 100, 101, 99, 100, 10_000))
    bars.append(_bar("20260522", current_open, 109, 104, current_close, current_volume))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "NVDA", "name": "NVIDIA", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()
    service = OverseasBuyableGapUpDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_buyable_gap_up(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "O'NeilBGU_overseas"
    assert sig["code"] == "NVDA"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_buyable_gap_up"
    assert sig["gap_ratio"] >= 4.0
    assert sig["volume_ratio"] >= 3.0
    assert sig["stop_price"] == 104.0
    assert sig["entry_price"] == 107.0


@pytest.mark.asyncio
async def test_scan_skips_when_gap_is_too_small(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars(current_open=103.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_close_fails_to_hold_open(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars(current_close=104.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_volume_does_not_clear_average_threshold(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars(current_volume=20_000)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_bgu_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "NVDA"
    assert kwargs["strategy_name"] == "O'NeilBGU_overseas"
    assert kwargs["signal_source"] == "overseas_bgu_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NYSE"
    assert kwargs["snapshot"]["bar"]["date"] == "20260522"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "NVDA", "name": "NVIDIA", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 5, "notional_usd": 535.0, "reason": "slot"})

    service = OverseasBuyableGapUpDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 5
    assert signals[0]["notional_usd"] == 535.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")


@pytest.mark.asyncio
async def test_scan_copies_fx_fields_from_sizing_result(svc):
    """사이징이 환율/원화 노출을 돌려주면 신호에 그대로 옮겨 담는다."""
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))
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
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))
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
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_bgu_bars()))

    signals = await svc.service.scan_dry_run(record=False)

    assert len(signals) == 1
    svc.journal.record.assert_not_called()
