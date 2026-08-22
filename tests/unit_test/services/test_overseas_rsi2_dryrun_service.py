"""해외 RSI2 dry-run 신호 서비스 테스트.

완성 일봉 기준으로 장기 상승추세 + RSI(2) 과매도 조건을 판정하고 shadow 저널에
would-be 신호만 기록한다. 해외 자동 주문 경로는 열지 않는다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_rsi2_dryrun_service import OverseasRSI2DryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _rsi2_bars(*, last_close=104.0, last_open=108.0):
    bars = []
    price = 80.0
    for i in range(205):
        price += 0.15
        bars.append(_bar(f"2025{i + 1:04d}", price - 0.2, price + 0.5, price - 0.5, price, 10_000))
    # RSI(2)를 낮추기 위한 연속 하락 2일. 마지막 종가는 200MA 위에 유지.
    bars.append(_bar("20260520", 112.0, 113.0, 106.0, 108.0, 12_000))
    bars.append(_bar("20260521", last_open, 109.0, 103.0, last_close, 13_000))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()
    service = OverseasRSI2DryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_rsi2_pullback(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "RSI2Pullback_overseas"
    assert sig["code"] == "MSFT"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_rsi2_pullback"
    assert sig["rsi2"] <= 10.0
    assert sig["ma_200d"] > 0
    assert sig["entry_price"] == 104.0
    assert sig["stop_price"] == pytest.approx(98.8)
    assert sig["target_ma_period"] == 5


@pytest.mark.asyncio
async def test_scan_skips_when_not_enough_history(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()[-20:]))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_rsi_above_threshold(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars(last_close=111.0, last_open=108.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_rsi2_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "MSFT"
    assert kwargs["strategy_name"] == "RSI2Pullback_overseas"
    assert kwargs["signal_source"] == "overseas_rsi2_dryrun"
    assert kwargs["snapshot"]["exchange"] == "NYSE"
    assert kwargs["snapshot"]["bar"]["date"] == "20260521"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "MSFT", "name": "Microsoft", "exchange": "NASD", "avg_trading_value": 100_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_rsi2_bars()))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 7, "notional_usd": 728.0, "reason": "slot"})

    service = OverseasRSI2DryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 7
    assert signals[0]["notional_usd"] == 728.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")
