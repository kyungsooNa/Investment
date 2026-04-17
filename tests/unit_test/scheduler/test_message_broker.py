# tests/unit_test/scheduler/test_message_broker.py
import asyncio
import pytest
from scheduler.ticket_queue.ticket import Ticket
from scheduler.ticket_queue.message_broker import MessageBroker


async def test_publish_and_consume_order():
    broker = MessageBroker()
    high = Ticket(priority=0, task_name="HIGH", payload={})
    low = Ticket(priority=100, task_name="LOW", payload={})

    await broker.publish(low)
    await broker.publish(high)

    first = await broker.consume()
    broker.task_done()
    second = await broker.consume()
    broker.task_done()

    assert first.task_name == "HIGH"
    assert second.task_name == "LOW"


async def test_publish_returns_false_when_full():
    broker = MessageBroker(maxsize=1)
    t1 = Ticket(priority=10, task_name="T1", payload={})
    t2 = Ticket(priority=20, task_name="T2", payload={})

    result1 = await broker.publish(t1)
    result2 = await broker.publish(t2)  # 큐 가득 참

    assert result1 is True
    assert result2 is False


async def test_join_resolves_after_task_done():
    broker = MessageBroker()
    t = Ticket(priority=50, task_name="T", payload={})
    await broker.publish(t)
    consumed = await broker.consume()
    broker.task_done()
    await asyncio.wait_for(broker.join(), timeout=1.0)  # 타임아웃 없이 완료돼야 함


async def test_qsize_and_empty():
    broker = MessageBroker()
    assert broker.empty is True
    await broker.publish(Ticket(priority=1, task_name="X", payload={}))
    assert broker.qsize == 1
    assert broker.empty is False
