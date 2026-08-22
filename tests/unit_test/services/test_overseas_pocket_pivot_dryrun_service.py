"""해외 Pocket Pivot dry-run 신호 서비스 테스트.

완성 일봉 기준으로 PP 조건을 판정하고 shadow 저널에 would-be 신호만 기록한다.
해외 자동 주문은 아직 열지 않는다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from services.overseas_pocket_pivot_dryrun_service import OverseasPocketPivotDryRunService


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _pp_bars(*, current_volume=1600, current_close=103.0):
    bars = []
    for i in range(10):
        # 하락일 최대 거래량 1500. 현재 거래량 1600이면 90% 허들 통과.
        bars.append(_bar(f"202605{1 + i:02d}", 104, 106, 99, 100, 1000 + i * 50))
    bars.append(_bar("20260511", 100, 104, 98, 101, current_volume))
    bars.append(_bar("20260512", 101, 106, 100, current_close, current_volume))
    return bars


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()
    service = OverseasPocketPivotDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_pocket_pivot(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars()))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    sig = signals[0]
    assert sig["strategy"] == "O'NeilPP_overseas"
    assert sig["code"] == "AAA"
    assert sig["action"] == "BUY"
    assert sig["entry_reason"] == "overseas_pocket_pivot"
    assert sig["supporting_ma"] in {"10", "20", "50"}
    assert sig["volume_ratio"] > 1.0
    assert sig["stop_price"] > 0


@pytest.mark.asyncio
async def test_scan_skips_when_volume_does_not_clear_down_day_threshold(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars(current_volume=800)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_skips_when_price_is_not_near_moving_average(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars(current_close=120.0)))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_passes_exchange_to_candidates_and_ohlcv(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    _, cand_kwargs = svc.candidate_service.get_candidates.call_args
    assert cand_kwargs.get("exchange") == OverseasExchange.NYSE or svc.candidate_service.get_candidates.call_args[0][0] == OverseasExchange.NYSE
    _, ohlcv_kwargs = svc.sqs.get_recent_daily_ohlcv.await_args
    assert ohlcv_kwargs.get("exchange") == OverseasExchange.NYSE


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_pp_source(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars()))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["code"] == "AAA"
    assert kwargs["strategy_name"] == "O'NeilPP_overseas"
    assert kwargs["signal_source"] == "overseas_pp_dryrun"
    assert kwargs["snapshot"]["bar"]["date"] == "20260512"


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(_pp_bars(current_close=103.0)))
    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 10, "notional_usd": 1000.0, "reason": "slot"})

    service = OverseasPocketPivotDryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 10
    assert signals[0]["notional_usd"] == 1000.0
    assert sizing.size.call_args.kwargs["limit_price_usd"] == signals[0]["entry_price"]
    assert not hasattr(service, "_order_execution_service")
