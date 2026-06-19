"""해외 VBO 게이팅 주문 실행 서비스 테스트 (Phase 4).

핵심 안전 계약: `live_enabled=False`(기본)에서는 broker 주문 메서드가 **절대 호출되지
않는다**(구조적 실주문 잠금). live_enabled=True 일 때만 실호출. dry-run 검증 + Phase 5
canary/kill-switch 가 이 플래그를 켜는 유일한 주체다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse
from services.overseas_order_execution_service import OverseasOrderExecutionService


def _broker(order_resp=None):
    broker = MagicMock()
    report = OverseasOrderReport(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="buy",
        qty=6, limit_price="150.0", broker_order_no="0001234",
    )
    broker.place_overseas_limit_order = AsyncMock(
        return_value=order_resp or ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=report
        )
    )
    return broker


# ── live_enabled=False: 구조적 실주문 잠금 ────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_default_does_not_call_broker():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=False)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    broker.place_overseas_limit_order.assert_not_called()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    report = resp.data
    assert report.side == "buy"
    assert report.qty == 6
    assert report.broker_order_no == ""  # would-be — 브로커 주문번호 없음
    assert report.raw.get("would_be") is True
    assert report.raw.get("signal_source") == OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER


@pytest.mark.asyncio
async def test_exit_default_does_not_call_broker():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=False)
    resp = await svc.place_exit(code="AAPL", qty=6, limit_price=148.0, reason="stop")
    broker.place_overseas_limit_order.assert_not_called()
    assert resp.data.side == "sell"
    assert resp.data.raw.get("exit_reason") == "stop"


@pytest.mark.asyncio
async def test_paper_mode_works_without_broker():
    """live_enabled=False 면 broker=None 이어도 would-be 주문이 가능해야 한다."""
    svc = OverseasOrderExecutionService(None, live_enabled=False)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.qty == 6


# ── live_enabled=True: 실호출 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_live_calls_broker_with_buy_side():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True,
                                        default_exchange=OverseasExchange.NASD)
    resp = await svc.place_entry(code="aapl", qty=6, limit_price=150.0)
    broker.place_overseas_limit_order.assert_awaited_once()
    kwargs = broker.place_overseas_limit_order.await_args.kwargs
    assert kwargs["side"] == "buy"
    assert kwargs["symbol"] == "AAPL"  # 대문자 정규화
    assert kwargs["qty"] == 6
    assert kwargs["limit_price"] == "150.0"
    assert kwargs["exchange"] == OverseasExchange.NASD
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.broker_order_no == "0001234"


@pytest.mark.asyncio
async def test_exit_live_calls_broker_with_sell_side():
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    await svc.place_exit(code="AAPL", qty=6, limit_price=148.0, reason="eod")
    kwargs = broker.place_overseas_limit_order.await_args.kwargs
    assert kwargs["side"] == "sell"


@pytest.mark.asyncio
async def test_live_propagates_broker_failure():
    broker = _broker(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="rejected", data=None))
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.API_ERROR.value


# ── 검증: 어떤 모드에서도 잘못된 입력은 broker 도달 전 차단 ────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("qty", [0, -1])
async def test_invalid_qty_rejected_before_broker(qty):
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=qty, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -5.0])
async def test_invalid_price_rejected_before_broker(price):
    broker = _broker()
    svc = OverseasOrderExecutionService(broker, live_enabled=True)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=price)
    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.place_overseas_limit_order.assert_not_called()


# ── kill-switch 게이트 (live 경로) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_live_blocked_by_kill_switch_does_not_call_broker():
    broker = _broker()
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "일일 손실 한도 초과"))
    svc = OverseasOrderExecutionService(broker, live_enabled=True, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_live_allowed_by_kill_switch_calls_broker():
    broker = _broker()
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(True, None))
    svc = OverseasOrderExecutionService(broker, live_enabled=True, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    broker.place_overseas_limit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_paper_mode_skips_kill_switch_check():
    """paper(live off)는 실주문이 없으므로 kill-switch 를 호출하지 않는다."""
    ks = MagicMock()
    ks.check_orders_allowed = AsyncMock(return_value=(False, "blocked"))
    svc = OverseasOrderExecutionService(None, live_enabled=False, kill_switch=ks)
    resp = await svc.place_entry(code="AAPL", qty=6, limit_price=150.0)
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    ks.check_orders_allowed.assert_not_called()


# ── 일봉 기반 exit 판정 (순수 로직) ───────────────────────────────────────────

def test_decide_daily_exit_stop_when_low_breaks_stop():
    out = OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0,
        daily_bar={"low": 96.0, "close": 99.0},
    )
    assert out == {"exit_price": 97.0, "exit_reason": "stop"}


def test_decide_daily_exit_eod_when_stop_not_hit():
    out = OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0,
        daily_bar={"low": 98.0, "close": 101.0},
    )
    assert out == {"exit_price": 101.0, "exit_reason": "eod"}


def test_decide_daily_exit_none_on_invalid_bar():
    assert OverseasOrderExecutionService.decide_daily_exit(
        entry_price=100.0, stop_price=97.0, daily_bar={}
    ) is None


# ── 저널 연동 (선택) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_records_order_when_provided():
    journal = MagicMock()
    svc = OverseasOrderExecutionService(None, live_enabled=False, journal=journal)
    await svc.place_entry(code="AAPL", qty=6, limit_price=150.0,
                          signal={"reason": "vbo_daily_breakout"})
    journal.record.assert_called_once()
    kwargs = journal.record.call_args.kwargs
    assert kwargs["code"] == "AAPL"
    assert kwargs["signal_source"] == OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER
