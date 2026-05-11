"""DeferredOrderQueue 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.deferred_order_queue import DeferredOrderQueue, EnqueueResult


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def fake_clock():
    """시간 조작 가능한 clock."""

    class Clock:
        def __init__(self):
            self.now = datetime(2026, 5, 11, 9, 0, 0)

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now = self.now + timedelta(seconds=seconds)

    return Clock()


@pytest.fixture
def queue(logger, fake_clock):
    return DeferredOrderQueue(logger, now_provider=fake_clock)


@pytest.mark.asyncio
async def test_enqueue_then_notify_runs_submit_callable(queue):
    submitted = AsyncMock()
    result = await queue.enqueue(
        stock_code="005930",
        side="BUY",
        submit_callable=submitted,
    )
    assert result == EnqueueResult.QUEUED
    assert queue.pending_count() == 1
    assert queue.has_pending("005930", "BUY")

    await queue.notify_terminal("005930")

    submitted.assert_awaited_once()
    assert queue.pending_count() == 0
    assert not queue.has_pending("005930", "BUY")


@pytest.mark.asyncio
async def test_duplicate_enqueue_dropped(queue, logger):
    submitted_first = AsyncMock()
    submitted_second = AsyncMock()

    r1 = await queue.enqueue(
        stock_code="005930", side="BUY", submit_callable=submitted_first
    )
    r2 = await queue.enqueue(
        stock_code="005930", side="BUY", submit_callable=submitted_second
    )
    assert r1 == EnqueueResult.QUEUED
    assert r2 == EnqueueResult.DUPLICATE_DROPPED
    assert queue.pending_count() == 1

    # 첫 번째 callable 만 실행되어야 함
    await queue.notify_terminal("005930")
    submitted_first.assert_awaited_once()
    submitted_second.assert_not_awaited()


@pytest.mark.asyncio
async def test_ttl_expired_drops_entry(queue, fake_clock):
    submitted = AsyncMock()
    await queue.enqueue(
        stock_code="005930",
        side="BUY",
        submit_callable=submitted,
        ttl_sec=30.0,
    )

    fake_clock.advance(31)  # TTL 초과
    await queue.notify_terminal("005930")

    submitted.assert_not_awaited()
    assert queue.pending_count() == 0


@pytest.mark.asyncio
async def test_risk_check_false_drops_entry(queue):
    submitted = AsyncMock()
    risk_check = AsyncMock(return_value=False)

    await queue.enqueue(
        stock_code="005930",
        side="BUY",
        submit_callable=submitted,
        risk_check=risk_check,
    )
    await queue.notify_terminal("005930")

    risk_check.assert_awaited_once()
    submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk_check_true_runs_submit(queue):
    submitted = AsyncMock()
    risk_check = AsyncMock(return_value=True)

    await queue.enqueue(
        stock_code="005930",
        side="BUY",
        submit_callable=submitted,
        risk_check=risk_check,
    )
    await queue.notify_terminal("005930")

    risk_check.assert_awaited_once()
    submitted.assert_awaited_once()


@pytest.mark.asyncio
async def test_risk_check_exception_drops_entry(queue):
    submitted = AsyncMock()
    risk_check = AsyncMock(side_effect=RuntimeError("boom"))

    await queue.enqueue(
        stock_code="005930",
        side="BUY",
        submit_callable=submitted,
        risk_check=risk_check,
    )
    await queue.notify_terminal("005930")

    submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_and_sell_separate_keys(queue):
    buy_cb = AsyncMock()
    sell_cb = AsyncMock()
    await queue.enqueue(stock_code="005930", side="BUY", submit_callable=buy_cb)
    await queue.enqueue(stock_code="005930", side="SELL", submit_callable=sell_cb)
    assert queue.pending_count() == 2

    await queue.notify_terminal("005930")
    buy_cb.assert_awaited_once()
    sell_cb.assert_awaited_once()
    assert queue.pending_count() == 0


@pytest.mark.asyncio
async def test_notify_other_stock_does_not_release(queue):
    submitted = AsyncMock()
    await queue.enqueue(stock_code="005930", side="BUY", submit_callable=submitted)
    await queue.notify_terminal("000660")  # 다른 종목

    submitted.assert_not_awaited()
    assert queue.pending_count() == 1


@pytest.mark.asyncio
async def test_submit_exception_logged_and_swallowed(queue):
    submitted = AsyncMock(side_effect=RuntimeError("submit failed"))
    await queue.enqueue(stock_code="005930", side="BUY", submit_callable=submitted)
    # 예외가 외부로 전파되지 않아야 함
    await queue.notify_terminal("005930")
    submitted.assert_awaited_once()
    assert queue.pending_count() == 0
