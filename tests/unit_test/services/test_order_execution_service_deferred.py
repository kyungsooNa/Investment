"""OrderExecutionService + DeferredOrderQueue 통합 시나리오 단위 테스트.

전략별 병렬 포지션 정책: 동일 종목에 진행 주문(non-terminal)이 있으면
신규 주문을 즉시 RETRY_LIMIT 으로 차단하지 않고 DeferredOrderQueue 로 보낸다.
기존 주문이 terminal 에 도달하면 자동 재시도된다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
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
from services.deferred_order_queue import DeferredOrderQueue
from services.order_execution_service import OrderExecutionService


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.debug = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()
        self.exception = MagicMock()


@pytest.fixture
def logger():
    return _Logger()


@pytest.fixture
def broker():
    mock = AsyncMock()
    mock.place_stock_order.return_value = ResCommonResponse(rt_cd="0", msg1="주문 성공", data=None)
    mock.env = MagicMock(is_paper_trading=True)
    return mock


@pytest.fixture
def market_clock():
    mock = MagicMock()
    mock.is_market_operating_hours.return_value = True
    mock.get_current_kst_time.return_value = datetime(2026, 5, 11, 10, 0, 0)
    return mock


@pytest.fixture
def market_calendar():
    mock = AsyncMock()
    mock.is_market_open_now.return_value = True
    return mock


@pytest.fixture
def deferred_queue(logger):
    return DeferredOrderQueue(logger)


@pytest.fixture
def oes(broker, logger, market_clock, market_calendar, deferred_queue):
    handler = OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        deferred_order_queue=deferred_queue,
    )
    return handler


def _seed_existing_order(handler: OrderExecutionService, *, stock_code: str, side: OrderSide, state: OrderState):
    """handler 의 _order_states 에 기존 주문 컨텍스트를 직접 삽입."""
    order_key = handler._make_order_key(stock_code, side, Exchange.KRX)
    ctx = OrderContext(
        order_key=order_key,
        stock_code=stock_code,
        side=side,
        state=state,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
        broker_order_no="X-001",
    )
    handler._order_states[order_key] = ctx
    if ctx.intent_id:
        handler._intent_index[ctx.intent_id] = order_key
    return ctx


@pytest.mark.asyncio
async def test_blocked_order_is_enqueued_and_returns_order_deferred(oes, deferred_queue):
    _seed_existing_order(oes, stock_code="005930", side=OrderSide.BUY, state=OrderState.SUBMITTED)

    result = await oes.handle_place_buy_order("005930", 70000, 5)

    assert result.rt_cd == ErrorCode.ORDER_DEFERRED.value
    assert deferred_queue.has_pending("005930", "BUY")
    assert deferred_queue.pending_count() == 1


@pytest.mark.asyncio
async def test_terminal_transition_triggers_deferred_retry(broker, logger, market_clock, market_calendar):
    # event loop 바인딩 문제를 피하기 위해 queue 와 oes 를 테스트 내부에서 생성.
    queue = DeferredOrderQueue(logger)
    handler = OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        deferred_order_queue=queue,
    )
    existing = _seed_existing_order(
        handler, stock_code="005930", side=OrderSide.BUY, state=OrderState.SUBMITTED
    )

    blocked = await handler.handle_place_buy_order("005930", 70000, 5)
    assert blocked.rt_cd == ErrorCode.ORDER_DEFERRED.value
    assert queue.has_pending("005930", "BUY")

    # 진행 주문이 FILLED 로 전이됨 → notify_terminal 이 비동기로 트리거되어야 함
    handler._transition_order_context(existing.order_key, OrderState.FILLED, filled_qty=10)

    # 백그라운드 task 들이 완료되도록 명시적으로 await
    pending = list(handler._notification_tasks)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=2.0)

    assert not queue.has_pending("005930", "BUY")
    broker.place_stock_order.assert_awaited()


@pytest.mark.asyncio
async def test_duplicate_deferred_falls_back_to_retry_limit(oes, deferred_queue):
    _seed_existing_order(oes, stock_code="005930", side=OrderSide.BUY, state=OrderState.SUBMITTED)

    first = await oes.handle_place_buy_order("005930", 70000, 5)
    assert first.rt_cd == ErrorCode.ORDER_DEFERRED.value

    second = await oes.handle_place_buy_order("005930", 70000, 7)
    # 같은 (code, side) 에 이미 보류 항목이 있으므로 RETRY_LIMIT 으로 차단
    assert second.rt_cd == ErrorCode.RETRY_LIMIT.value
    assert deferred_queue.pending_count() == 1


@pytest.mark.asyncio
async def test_no_deferred_queue_keeps_legacy_retry_limit_behavior(broker, logger, market_clock, market_calendar):
    handler = OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logger,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        # deferred_order_queue 미주입
    )
    _seed_existing_order(handler, stock_code="005930", side=OrderSide.BUY, state=OrderState.SUBMITTED)

    result = await handler.handle_place_buy_order("005930", 70000, 5)
    assert result.rt_cd == ErrorCode.RETRY_LIMIT.value
