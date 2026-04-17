# tests/unit_test/scheduler/test_time_dispatcher.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.dispatcher.time_dispatcher import TimeDispatcher


def _make_dispatcher(is_operating: bool, latest_date: str | None):
    broker = MessageBroker()
    market_clock = MagicMock()
    market_clock.is_market_operating_hours.return_value = is_operating
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_date)
    dispatcher = TimeDispatcher(broker=broker, market_clock=market_clock, mcs=mcs)
    return dispatcher, broker


async def test_no_ticket_during_market_hours():
    dispatcher, broker = _make_dispatcher(is_operating=True, latest_date="20250417")
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    result = await dispatcher._maybe_dispatch(None)
    assert result is None
    assert broker.empty is True


async def _drain(dispatcher) -> None:
    """pending_publish_tasks가 모두 완료될 때까지 대기한다."""
    pending = list(dispatcher._pending_publish_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_ticket_published_after_market_close():
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417")
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    result = await dispatcher._maybe_dispatch(None)
    await _drain(dispatcher)
    assert result == "20250417"
    assert broker.qsize == 1

    ticket = await broker.consume()
    broker.task_done()
    assert ticket.task_name == "RANKING_UPDATE"
    assert ticket.payload["date"] == "20250417"


async def test_no_duplicate_ticket_same_date():
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date="20250417")
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    await dispatcher._maybe_dispatch(None)
    await _drain(dispatcher)
    result2 = await dispatcher._maybe_dispatch("20250417")  # 이미 발행된 날짜

    assert result2 == "20250417"
    assert broker.qsize == 1  # 1개만 발행됨


async def test_no_ticket_when_mcs_returns_none():
    dispatcher, broker = _make_dispatcher(is_operating=False, latest_date=None)
    dispatcher.register_task("RANKING_UPDATE", priority=100)

    result = await dispatcher._maybe_dispatch(None)
    assert result is None
    assert broker.empty is True


async def test_stop_exits_run_loop():
    """stop()이 호출되면 POLL_INTERVAL 대기 중에도 즉시 루프를 빠져나온다."""
    dispatcher, _ = _make_dispatcher(is_operating=True, latest_date="20250417")
    dispatcher.POLL_INTERVAL = 3600  # 긴 sleep — stop()이 즉시 깨워야 함

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0)  # 루프가 시작되도록 한 번 yield
    dispatcher.stop()
    await asyncio.wait_for(run_task, timeout=1.0)
    assert True  # hang 없이 종료됨
