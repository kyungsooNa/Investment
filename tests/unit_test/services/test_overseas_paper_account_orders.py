"""해외 자동 전략 → 모의계좌 실주문 경로 테스트.

`live_enabled`(실계좌 실주문) 잠금은 그대로 두고, 앱이 **모의투자 모드일 때만** 자동
전략 주문을 KIS 모의계좌로 실제 전송한다. 모드는 **주문마다** 다시 확인한다 — 웹에서
실전으로 전환된 뒤 남은 인스턴스가 실계좌로 쏘면 안 된다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse
from services.overseas_order_execution_service import OverseasOrderExecutionService


def _broker():
    broker = MagicMock()
    report = OverseasOrderReport(
        symbol="AAPL", exchange=OverseasExchange.NASD, side="buy",
        qty=2, limit_price="150.00", broker_order_no="0001234",
    )
    broker.place_overseas_limit_order = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=report)
    )
    return broker


def _svc(broker, *, paper=True, journal=None, kill_switch=None, notification_service=None):
    state = {"paper": paper}
    svc = OverseasOrderExecutionService(
        broker,
        live_enabled=False,
        paper_account_orders=True,
        is_paper_trading_provider=lambda: state["paper"],
        journal=journal,
        kill_switch=kill_switch,
        notification_service=notification_service,
    )
    return svc, state


@pytest.mark.asyncio
async def test_paper_mode_sends_order_to_broker():
    broker = _broker()
    svc, _ = _svc(broker)

    resp = await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)

    broker.place_overseas_limit_order.assert_awaited_once()
    assert resp.data.broker_order_no == "0001234"


@pytest.mark.asyncio
async def test_real_mode_keeps_would_be_only():
    broker = _broker()
    svc, _ = _svc(broker, paper=False)

    resp = await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)

    broker.place_overseas_limit_order.assert_not_called()
    assert resp.data.raw.get("would_be") is True


@pytest.mark.asyncio
async def test_mode_is_rechecked_on_every_order():
    broker = _broker()
    svc, state = _svc(broker)
    await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)
    state["paper"] = False   # 웹에서 실전 전환

    await svc.place_exit(code="AAPL", qty=2, limit_price=151.0, reason="eod")

    assert broker.place_overseas_limit_order.await_count == 1


@pytest.mark.asyncio
async def test_mode_lookup_failure_is_fail_closed():
    broker = _broker()

    def _boom():
        raise RuntimeError("env 조회 실패")

    svc = OverseasOrderExecutionService(
        broker, live_enabled=False, paper_account_orders=True, is_paper_trading_provider=_boom,
    )
    resp = await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)

    broker.place_overseas_limit_order.assert_not_called()
    assert resp.data.raw.get("would_be") is True


@pytest.mark.asyncio
async def test_disabled_flag_keeps_would_be_even_in_paper_mode():
    broker = _broker()
    svc = OverseasOrderExecutionService(
        broker, live_enabled=False, paper_account_orders=False,
        is_paper_trading_provider=lambda: True,
    )
    await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_missing_broker_keeps_would_be():
    svc = OverseasOrderExecutionService(
        None, live_enabled=False, paper_account_orders=True,
        is_paper_trading_provider=lambda: True,
    )
    resp = await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)
    assert resp.data.raw.get("would_be") is True


@pytest.mark.asyncio
async def test_kill_switch_blocks_paper_account_orders():
    broker = _broker()
    kill_switch = MagicMock()
    kill_switch.check_orders_allowed = AsyncMock(return_value=(False, "테스트 차단"))
    svc, _ = _svc(broker, kill_switch=kill_switch)

    resp = await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)

    assert resp.rt_cd == ErrorCode.KILL_SWITCH_BLOCKED.value
    broker.place_overseas_limit_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("side,price,expected", [
    ("buy", 106.123, "106.13"),     # 매수는 올림 — 폴링가보다 낮게 내면 체결이 안 된다
    ("sell", 105.682, "105.68"),    # 매도는 내림
    ("buy", 150.0, "150.00"),
    ("sell", 0.123456, "0.1234"),   # $1 미만은 소수 4자리
])
async def test_broker_limit_price_is_rounded_to_tick(side, price, expected):
    broker = _broker()
    svc, _ = _svc(broker)
    if side == "buy":
        await svc.place_entry(code="AAPL", qty=2, limit_price=price)
    else:
        await svc.place_exit(code="AAPL", qty=2, limit_price=price, reason="stop")

    assert broker.place_overseas_limit_order.call_args.kwargs["limit_price"] == expected


@pytest.mark.asyncio
async def test_journal_marks_paper_account_source():
    broker = _broker()
    journal = MagicMock()
    svc, _ = _svc(broker, journal=journal)

    await svc.place_entry(code="AAPL", qty=2, limit_price=150.0)

    assert journal.record.call_args.kwargs["signal_source"] == (
        OverseasOrderExecutionService.SIGNAL_SOURCE_PAPER_ACCOUNT
    )


@pytest.mark.asyncio
async def test_notification_labels_paper_account_mode():
    broker = _broker()
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    svc, _ = _svc(broker, notification_service=notifier)

    await svc.place_entry(code="AAPL", qty=2, limit_price=150.0, signal={"strategy": "X"})

    message = notifier.emit.call_args.args[3]
    assert "모드: 모의투자(모의계좌 주문)" in message
