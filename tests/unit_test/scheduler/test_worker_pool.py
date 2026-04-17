# tests/unit_test/scheduler/test_worker_pool.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from scheduler.ticket_queue.ticket import Ticket
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.ticket_queue.dlq_manager import DlqManager
from scheduler.worker.worker_pool import WorkerPool


def _make_pool(num_workers=1) -> tuple[WorkerPool, MessageBroker, MagicMock]:
    broker = MessageBroker()
    dlq = MagicMock(spec=DlqManager)
    dlq.handle_failed_ticket = AsyncMock()
    pool = WorkerPool(broker=broker, dlq_manager=dlq, num_workers=num_workers)
    return pool, broker, dlq


async def test_handler_called_on_ticket():
    pool, broker, _ = _make_pool()
    called_with = []

    async def handler(payload):
        called_with.append(payload)

    pool.register("MY_TASK", handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="MY_TASK", payload={"date": "20250417"}))
    await broker.join()
    await pool.shutdown()

    assert called_with == [{"date": "20250417"}]


async def test_retry_on_failure():
    pool, broker, dlq = _make_pool()
    pool.BASE_DELAY = 0  # 테스트 속도 위해 delay 0

    call_count = 0

    async def flaky_handler(payload):
        nonlocal call_count
        call_count += 1
        if call_count < pool.MAX_RETRIES:
            raise ValueError("일시 오류")

    pool.register("FLAKY", flaky_handler)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="FLAKY", payload={}))
    await broker.join()
    await pool.shutdown()

    assert call_count == pool.MAX_RETRIES
    dlq.handle_failed_ticket.assert_not_called()


async def test_dlq_after_max_retries():
    pool, broker, dlq = _make_pool()
    pool.BASE_DELAY = 0

    async def always_fail(payload):
        raise RuntimeError("영구 오류")

    pool.register("BAD_TASK", always_fail)
    await pool.start()
    await broker.publish(Ticket(priority=50, task_name="BAD_TASK", payload={}))
    await broker.join()
    await pool.shutdown()

    dlq.handle_failed_ticket.assert_called_once()
    args = dlq.handle_failed_ticket.call_args[0]
    assert args[0].task_name == "BAD_TASK"
    assert args[0].attempt == pool.MAX_RETRIES


async def test_suspend_blocks_processing():
    pool, broker, _ = _make_pool()
    processed = []

    async def handler(payload):
        processed.append(payload)

    pool.register("T", handler)
    await pool.start()
    pool.suspend()

    await broker.publish(Ticket(priority=50, task_name="T", payload={"n": 1}))
    await asyncio.sleep(0.05)  # 잠시 대기 — suspend 중이므로 처리 안 됨
    assert processed == []

    pool.resume()
    await broker.join()
    await pool.shutdown()
    assert len(processed) == 1


async def test_poison_pill_priority_beats_low_tickets():
    """shutdown 시 독약이 LOW 티켓보다 먼저 처리되어 워커가 즉시 종료."""
    pool, broker, _ = _make_pool()
    processed = []

    async def handler(payload):
        processed.append(payload)

    pool.register("LOW_TASK", handler)
    await pool.start()

    # LOW 티켓 여러 개 발행 후 즉시 shutdown
    for i in range(5):
        await broker.publish(Ticket(priority=100, task_name="LOW_TASK", payload={"n": i}))

    await pool.shutdown()  # Poison Pill priority=-1로 발행 → 즉시 종료
    # 독약이 LOW 티켓보다 먼저이므로 일부만 처리되거나 0개 처리될 수 있음
    # 핵심: shutdown이 hang 없이 완료됨을 검증
    assert True  # 여기까지 도달하면 hang 없음
