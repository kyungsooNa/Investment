"""해외 VBO dry-run 신호 서비스 테스트 (Phase 3).

후보(Phase 1-3) → 일봉(Phase 1-1 어댑터) → VBO 일봉 진입 규칙(Phase 2) → shadow 저널.
**주문 경로 없음**: order_execution 미주입, 저널 기록만. 해외 주문은 실전 TR만
존재하므로 dry-run 단계에서 실주문이 절대 발생하지 않음을 보장한다.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from services.overseas_vbo_dryrun_service import OverseasVBODryRunService
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _ohlcv(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


@pytest.fixture
def svc():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    journal = MagicMock()

    service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=journal,
        logger=MagicMock(),
        k_value=0.5,
        stop_loss_pct=-3.0,
    )
    return SimpleNamespace(service=service, candidate_service=candidate_service, sqs=sqs, journal=journal)


@pytest.mark.asyncio
async def test_scan_emits_buy_on_breakout(svc):
    # prev range 10 → target = 100+5 = 105, 당일고 120 >= 105 → BUY
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert len(signals) == 1
    assert signals[0]["code"] == "AAA"
    assert signals[0]["action"] == "BUY"
    assert signals[0]["target"] == 105.0


@pytest.mark.asyncio
async def test_scan_emits_same_day_eod_exit(svc):
    """돌파 후 당일저가 손절가 미터치 → 당일 종가(eod) 청산을 동봉한다(Phase 2 모델 동일)."""
    # target=105, stop=105*0.97=101.85, 당일저 104 > stop → eod 청산(종가 115)
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["exit_reason"] == "eod"
    assert signals[0]["exit_price"] == 115.0
    assert signals[0]["realized_pct"] == pytest.approx((115.0 / 105.0 - 1) * 100)


@pytest.mark.asyncio
async def test_scan_emits_same_day_stop_exit(svc):
    """돌파 후 당일저가 손절가 터치 → 손절가(stop) 청산을 동봉한다."""
    # target=105, stop=101.85, 당일저 100 <= stop → stop 청산
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 100, 102)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["exit_reason"] == "stop"
    assert signals[0]["exit_price"] == pytest.approx(101.85)
    assert signals[0]["realized_pct"] == pytest.approx(-3.0)


@pytest.mark.asyncio
async def test_scan_no_signal_when_no_breakout(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 104, 100, 103)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []
    svc.journal.record.assert_not_called()


@pytest.mark.asyncio
async def test_scan_records_to_shadow_journal_with_dryrun_source(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    svc.journal.record.assert_called_once()
    _, kwargs = svc.journal.record.call_args
    assert kwargs["signal_source"] == "overseas_dryrun"
    assert kwargs["code"] == "AAA"


@pytest.mark.asyncio
async def test_scan_skips_insufficient_bars(svc):
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv([_bar("20260512", 100, 120, 104, 115)]))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals == []


@pytest.mark.asyncio
async def test_scan_passes_overseas_exchange_downstream(svc):
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    await svc.service.scan_dry_run(exchange=OverseasExchange.NYSE)

    # 후보 조회와 일봉 조회 모두 해외 거래소 인자로 위임
    _, cand_kwargs = svc.candidate_service.get_candidates.call_args
    assert cand_kwargs.get("exchange") == OverseasExchange.NYSE or svc.candidate_service.get_candidates.call_args[0][0] == OverseasExchange.NYSE
    _, ohlcv_kwargs = svc.sqs.get_recent_daily_ohlcv.await_args
    assert ohlcv_kwargs.get("exchange") == OverseasExchange.NYSE


@pytest.mark.asyncio
async def test_scan_has_no_order_path(svc):
    """서비스는 order_execution 의존을 갖지 않는다(실주문 불가 보장)."""
    assert not hasattr(svc.service, "_order_execution_service")
    assert not hasattr(svc.service, "_order_service")


@pytest.mark.asyncio
async def test_scan_omits_qty_when_no_sizing(svc):
    """사이징 서비스 미주입 시 신호에 qty 를 넣지 않는다(하위 호환)."""
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    svc.sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    signals = await svc.service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert "qty" not in signals[0]


@pytest.mark.asyncio
async def test_scan_includes_qty_when_sizing_injected():
    """사이징 서비스 주입 시 would-be qty/notional 을 신호에 동봉한다(주문 경로 없음)."""
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[
        {"code": "AAA", "name": "Aaa", "exchange": "NASD", "avg_trading_value": 10_000_000.0},
    ])
    sqs = MagicMock()
    bars = [_bar("20260511", 100, 110, 100, 105), _bar("20260512", 100, 120, 104, 115)]
    sqs.get_recent_daily_ohlcv = AsyncMock(return_value=_ohlcv(bars))

    sizing = MagicMock()
    sizing.size = MagicMock(return_value={"qty": 9, "notional_usd": 945.0, "reason": "slot"})

    service = OverseasVBODryRunService(
        candidate_service=candidate_service,
        stock_query_service=sqs,
        shadow_journal=MagicMock(),
        logger=MagicMock(),
        position_sizing_service=sizing,
    )

    signals = await service.scan_dry_run(exchange=OverseasExchange.NASD)

    assert signals[0]["qty"] == 9
    assert signals[0]["notional_usd"] == 945.0
    # entry_price(=target 105) 로 사이징 호출
    _, kwargs = sizing.size.call_args
    assert kwargs["limit_price_usd"] == 105.0
    # 사이징 주입돼도 order_execution 의존은 없다
    assert not hasattr(service, "_order_execution_service")
