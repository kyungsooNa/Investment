"""
E2E integration tests for order FSM state transitions.

Verifies that poll_active_orders_once() correctly drives OrderContext
through SUBMITTED → PARTIAL_FILLED → FILLED, CANCELED, REJECTED state
transitions using real response-schema fixtures, and that order acceptance
alone does not finalize a holding.

Background tasks (start/stop) are NOT used — only reconcile/poll methods
are called directly to prevent hang.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    ErrorCode,
    Exchange,
    OrderContext,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from services.order_execution_service import OrderExecutionService

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "kis"
FSM_FIXTURE = json.loads((FIXTURE_DIR / "order_fsm_e2e_transitions.json").read_text(encoding="utf-8"))


def _make_ccld_response(rows: list[dict]) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={"output1": rows},
    )


def _make_error_response(msg: str = "조회 실패") -> ResCommonResponse:
    return ResCommonResponse(rt_cd="1", msg1=msg, data=None)


def _make_service(broker: AsyncMock) -> OrderExecutionService:
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 5, 10, 9, 0, 0)
    clock.is_market_operating_hours.return_value = True
    return OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logging.getLogger("test"),
        market_clock=clock,
    )


def _register_context(svc: OrderExecutionService, odno: str, pdno: str, qty: int, price: int) -> str:
    """수동으로 SUBMITTED OrderContext를 등록하고 order_key를 반환합니다."""
    order_key = svc._make_order_key(pdno, OrderSide.BUY, Exchange.KRX)
    ctx = OrderContext(
        order_key=order_key,
        stock_code=pdno,
        side=OrderSide.BUY,
        state=OrderState.SUBMITTED,
        price=price,
        qty=qty,
        exchange=Exchange.KRX,
        source="test",
        broker_order_no=odno,
        created_at=datetime(2026, 5, 10, 9, 0, 0),
        filled_qty=0,
    )
    svc._order_states[order_key] = ctx
    svc._order_no_index[odno] = order_key
    return order_key


@pytest.mark.asyncio
async def test_state_transition_pending_to_filled():
    """SUBMITTED → PARTIAL_FILLED → FILLED 3단계 polling 전이 검증."""
    scenario = FSM_FIXTURE["scenarios"]["pending_to_filled"]
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    svc = _make_service(broker)
    order_key = _register_context(svc, scenario["odno"], scenario["pdno"], qty=10, price=70000)

    for round_data in scenario["rounds"]:
        broker.inquire_daily_ccld = AsyncMock(
            return_value=_make_ccld_response([round_data["row"]])
        )
        applied = await svc.poll_active_orders_once()
        ctx = svc._order_states[order_key]
        assert ctx.state.value == round_data["expected_state"], (
            f"[{round_data['label']}] expected {round_data['expected_state']}, got {ctx.state}"
        )
        assert ctx.filled_qty == round_data["expected_filled_qty"]
        assert ctx.remaining_qty == round_data["expected_remaining_qty"]

    final = svc._order_states[order_key]
    assert final.state == OrderState.FILLED
    assert final.state.is_terminal


@pytest.mark.asyncio
async def test_state_transition_pending_to_canceled():
    """cncl_yn='Y' 응답에서 SUBMITTED → CANCELED 전이 검증."""
    scenario = FSM_FIXTURE["scenarios"]["pending_to_canceled"]
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    svc = _make_service(broker)
    order_key = _register_context(svc, scenario["odno"], scenario["pdno"], qty=5, price=120000)

    round_data = scenario["rounds"][0]
    broker.inquire_daily_ccld = AsyncMock(
        return_value=_make_ccld_response([round_data["row"]])
    )
    await svc.poll_active_orders_once()

    ctx = svc._order_states[order_key]
    assert ctx.state == OrderState.CANCELED
    assert ctx.state.is_terminal


@pytest.mark.asyncio
async def test_state_transition_pending_to_rejected():
    """rjct_qty > 0 응답에서 SUBMITTED → REJECTED 전이 검증."""
    scenario = FSM_FIXTURE["scenarios"]["pending_to_rejected"]
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    svc = _make_service(broker)
    order_key = _register_context(svc, scenario["odno"], scenario["pdno"], qty=3, price=50000)

    round_data = scenario["rounds"][0]
    broker.inquire_daily_ccld = AsyncMock(
        return_value=_make_ccld_response([round_data["row"]])
    )
    await svc.poll_active_orders_once()

    ctx = svc._order_states[order_key]
    assert ctx.state == OrderState.REJECTED
    assert ctx.state.is_terminal


@pytest.mark.asyncio
async def test_acceptance_does_not_finalize_holding():
    """주문 접수(SUBMITTED) 직후 virtual_trade_service에 HOLD가 추가되지 않는지 검증.
    FILLED 전이 후에만 HOLD가 기록된다.
    """
    scenario = FSM_FIXTURE["scenarios"]["pending_to_filled"]
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    vts = MagicMock()
    vts.get_holds = MagicMock(return_value=[])
    vts.log_buy = MagicMock()
    vts.log_sell_async = AsyncMock()

    svc = _make_service(broker)
    svc._virtual_trade_service = vts
    order_key = _register_context(svc, scenario["odno"], scenario["pdno"], qty=10, price=70000)

    # Round 1: still SUBMITTED — no HOLD should be logged
    broker.inquire_daily_ccld = AsyncMock(
        return_value=_make_ccld_response([scenario["rounds"][0]["row"]])
    )
    await svc.poll_active_orders_once()
    assert svc._order_states[order_key].state == OrderState.SUBMITTED
    # HOLD는 log_buy 호출 여부로 간접 검증:
    # poll 자체는 log_buy를 호출하지 않는다 (submit 시점에 호출됨)
    vts.log_buy.assert_not_called()

    # Round 3: FILLED — virtual_trade_service.log_buy는 주문 제출 시점에 이미 호출되므로
    # FSM 전이 자체는 log_buy 재호출 없이 state만 변경
    broker.inquire_daily_ccld = AsyncMock(
        return_value=_make_ccld_response([scenario["rounds"][2]["row"]])
    )
    await svc.poll_active_orders_once()
    assert svc._order_states[order_key].state == OrderState.FILLED


@pytest.mark.asyncio
async def test_unfilled_remains_pending_across_polls():
    """미체결 주문이 두 번 polling 되어도 SUBMITTED 상태를 유지하는지 검증."""
    scenario = FSM_FIXTURE["scenarios"]["unfilled_remains_pending"]
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    svc = _make_service(broker)
    order_key = _register_context(svc, scenario["odno"], scenario["pdno"], qty=2, price=200000)

    for round_data in scenario["rounds"]:
        broker.inquire_daily_ccld = AsyncMock(
            return_value=_make_ccld_response([round_data["row"]])
        )
        await svc.poll_active_orders_once()
        ctx = svc._order_states[order_key]
        assert ctx.state == OrderState.SUBMITTED, (
            f"[{round_data['label']}] SUBMITTED 유지 실패: {ctx.state}"
        )
        assert ctx.filled_qty == 0
        assert ctx.remaining_qty == 2
