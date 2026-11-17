import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from interfaces.schedulable_task import TaskState
from task.background.always_on.heartbeat_ping_task import HeartbeatPingTask

URL = "https://hc-ping.example/secret-uuid"


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout)


@pytest.mark.asyncio
async def test_ping_once_records_success():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="OK")

    task = HeartbeatPingTask(url=URL, logger=MagicMock(), client_factory=_client_factory(handler))

    assert await task.ping_once() is True

    assert requests == [URL]
    progress = task.get_progress()
    assert progress["consecutive_failures"] == 0
    assert progress["last_ok_at"] is not None


@pytest.mark.asyncio
async def test_ping_failure_is_logged_without_url_and_does_not_raise():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    logger = MagicMock()
    task = HeartbeatPingTask(url=URL, logger=logger, client_factory=_client_factory(handler))

    assert await task.ping_once() is False
    assert await task.ping_once() is False

    assert task.get_progress()["consecutive_failures"] == 2
    logged = " ".join(str(call) for call in logger.warning.call_args_list)
    assert "secret-uuid" not in logged


@pytest.mark.asyncio
async def test_non_2xx_response_counts_as_failure():
    task = HeartbeatPingTask(
        url=URL,
        logger=MagicMock(),
        client_factory=_client_factory(lambda request: httpx.Response(404)),
    )

    assert await task.ping_once() is False
    assert task.get_progress()["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_suspend_keeps_pinging_to_avoid_false_dead_alarm():
    """포그라운드 작업이 suspend_all 을 걸어도 heartbeat 는 멈추면 안 된다(오탐 알림)."""
    pings = []
    loop = asyncio.get_running_loop()

    async def fake_sleep(_seconds):
        if len(pings) >= 3:
            raise asyncio.CancelledError
        # 실제 시간 없이 이벤트 루프에 한 번 양보한다 (conftest 의 asyncio.sleep mock 과 무관).
        future = loop.create_future()
        loop.call_soon(future.set_result, None)
        await future

    def handler(request):
        pings.append(request.url)
        return httpx.Response(200)

    task = HeartbeatPingTask(
        url=URL, logger=MagicMock(), client_factory=_client_factory(handler), sleep=fake_sleep
    )
    await task.start()
    await task.suspend()
    assert task.state == TaskState.RUNNING

    await asyncio.wait_for(task._tasks[0], timeout=5)
    await task.stop()

    assert len(pings) == 3
    assert task.state == TaskState.STOPPED


def test_build_heartbeat_task_disabled_without_url():
    from view.web.bootstrap.heartbeat_bootstrap import build_heartbeat_task

    ctx = SimpleNamespace(full_config={}, logger=MagicMock())

    assert build_heartbeat_task(ctx) is None
    assert ctx.heartbeat_task is None


def test_build_heartbeat_task_from_config():
    from view.web.bootstrap.heartbeat_bootstrap import build_heartbeat_task

    ctx = SimpleNamespace(
        full_config={"heartbeat": {"url": URL, "interval_sec": 30}},
        logger=MagicMock(),
    )

    task = build_heartbeat_task(ctx)

    assert isinstance(task, HeartbeatPingTask)
    assert ctx.heartbeat_task is task
    assert task.get_progress()["interval_sec"] == 30
