"""
NotificationQueueTask 단위 테스트.

검증 범위:
  - 태스크 기본 속성 (task_name, priority)
  - 라이프사이클 (start / stop / suspend / resume)
  - drain_loop: 이벤트 순차 전달, 다중 핸들러, 예외 격리
  - suspend 중 큐 누적 → resume 후 소비
  - get_progress 반환값
"""
import asyncio
import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.always_on.notification_queue_task import NotificationQueueTask
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel
from interfaces.schedulable_task import TaskState, TaskPriority


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_clock():
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    return clock


@pytest.fixture
def ns(mock_clock):
    """테스트용 NotificationService."""
    return NotificationService(market_clock=mock_clock)


@pytest.fixture
def queue_task(ns):
    """핸들러 없이 생성된 NotificationQueueTask."""
    return NotificationQueueTask(
        notification_service=ns,
        poll_interval=0,
        logger=MagicMock(),
    )


# ── 기본 속성 ────────────────────────────────────────────────────────────────


def test_task_name(queue_task):
    assert queue_task.task_name == "notification_queue"


def test_priority_is_low(queue_task):
    assert queue_task.priority == TaskPriority.LOW


def test_initial_state_is_idle(queue_task):
    assert queue_task.state == TaskState.IDLE


# ── 라이프사이클 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_sets_running_state(queue_task):
    await queue_task.start()
    assert queue_task.state == TaskState.RUNNING
    await queue_task.stop()


@pytest.mark.asyncio
async def test_start_idempotent(queue_task):
    """이미 RUNNING 상태에서 start()를 다시 호출해도 drain 태스크가 중복 생성되지 않아야 한다."""
    await queue_task.start()
    task_count_before = len(queue_task._tasks)
    await queue_task.start()
    assert len(queue_task._tasks) == task_count_before
    await queue_task.stop()


@pytest.mark.asyncio
async def test_stop_sets_stopped_state_and_clears_tasks(queue_task):
    await queue_task.start()
    await queue_task.stop()
    assert queue_task.state == TaskState.STOPPED
    assert queue_task._tasks == []


@pytest.mark.asyncio
async def test_stop_without_started_sets_stopped_state(queue_task):
    await queue_task.stop()
    assert queue_task.state == TaskState.STOPPED
    assert queue_task._tasks == []


@pytest.mark.asyncio
async def test_stop_keeps_already_done_task_uncancelled(queue_task):
    done_task = asyncio.Future()
    done_task.set_result(None)
    done_task.cancel = MagicMock()
    queue_task._tasks = [done_task]

    await queue_task.stop()

    done_task.cancel.assert_not_called()
    assert queue_task.state == TaskState.STOPPED
    assert queue_task._tasks == []


@pytest.mark.asyncio
async def test_suspend_sets_suspended_state(queue_task):
    await queue_task.start()
    await queue_task.suspend()
    assert queue_task.state == TaskState.SUSPENDED
    await queue_task.stop()


@pytest.mark.asyncio
async def test_resume_sets_running_state(queue_task):
    await queue_task.start()
    await queue_task.suspend()
    await queue_task.resume()
    assert queue_task.state == TaskState.RUNNING
    await queue_task.stop()


@pytest.mark.asyncio
async def test_suspend_noop_when_not_running(queue_task):
    """IDLE 상태에서 suspend()를 호출해도 상태가 변경되지 않아야 한다."""
    assert queue_task.state == TaskState.IDLE
    await queue_task.suspend()
    assert queue_task.state == TaskState.IDLE


@pytest.mark.asyncio
async def test_suspend_running_without_resume_event(queue_task):
    queue_task._state = TaskState.RUNNING
    queue_task._resume_event = None

    await queue_task.suspend()

    assert queue_task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_resume_noop_when_running(queue_task):
    """RUNNING 상태에서 resume()를 호출해도 상태가 변경되지 않아야 한다."""
    await queue_task.start()
    await queue_task.resume()
    assert queue_task.state == TaskState.RUNNING
    await queue_task.stop()


# ── get_progress ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_suspended_without_resume_event(queue_task):
    queue_task._state = TaskState.SUSPENDED
    queue_task._resume_event = None

    await queue_task.resume()

    assert queue_task.state == TaskState.RUNNING


@pytest.mark.asyncio
async def test_get_progress_running_state(queue_task, ns):
    await queue_task.start()
    progress = queue_task.get_progress()
    assert progress["running"] is True
    assert progress["queued_events"] == 0
    await queue_task.stop()


@pytest.mark.asyncio
async def test_get_progress_reflects_queue_size(ns):
    """emit() 후 get_progress의 queued_events가 큐 크기를 반영해야 한다."""
    # 핸들러를 등록해야 emit()이 external_handler_queue에 적재한다
    ns.register_external_handler(AsyncMock())
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    await task.start()
    await task.suspend()  # 처리 중단 후 이벤트 적재

    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "t", "m")
    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "t", "m")

    assert task.get_progress()["queued_events"] == 2
    await task.stop()


# ── drain_loop: 핸들러 호출 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_loop_calls_all_handlers(ns):
    """이벤트 발생 시 등록된 모든 외부 핸들러가 호출된다."""
    handler1 = AsyncMock()
    handler2 = AsyncMock()
    ns.register_external_handler(handler1)
    ns.register_external_handler(handler2)
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    event = await ns.emit(NotificationCategory.STRATEGY, NotificationLevel.INFO, "시그널", "삼성전자 매수")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    handler1.assert_awaited_once_with(event)
    handler2.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_drain_loop_processes_events_in_order(ns):
    """여러 이벤트가 emit() 순서대로 처리된다."""
    call_order = []

    async def tracking_handler(event):
        call_order.append(event.title)

    ns.register_external_handler(tracking_handler)
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "first", "m")
    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "second", "m")
    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "third", "m")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    assert call_order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_drain_loop_handler_exception_does_not_stop_processing(ns):
    """핸들러 하나가 예외를 발생시켜도 다음 핸들러와 다음 이벤트가 계속 처리된다."""
    fail_handler = AsyncMock(side_effect=Exception("handler crash"))
    ok_handler = AsyncMock()

    ns.register_external_handler(fail_handler)
    ns.register_external_handler(ok_handler)
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    event = await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "장애", "m")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    # 실패 핸들러도 호출되었고, 이후 정상 핸들러도 호출되어야 함
    fail_handler.assert_awaited_once_with(event)
    ok_handler.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_drain_loop_filters_telegram_by_category_level(ns):
    handler = AsyncMock()
    ns.register_external_handler(handler)
    task = NotificationQueueTask(
        ns,
        poll_interval=0,
        telegram_config=SimpleNamespace(
            enabled=True,
            route_levels={
                "SYSTEM": ["error", "critical"],
                "TRADE": ["warning", "error", "critical"],
            },
        ),
        logger=MagicMock(),
    )

    info_event = await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "info", "web only")
    warning_event = await ns.emit(NotificationCategory.TRADE, NotificationLevel.WARNING, "warn", "telegram")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    handler.assert_awaited_once_with(warning_event)
    assert info_event.title == "info"


@pytest.mark.asyncio
async def test_drain_loop_sends_force_external_event_even_when_level_filtered(ns):
    """metadata.force_external=True 이벤트는 레벨 라우팅에서 제외돼도 외부 핸들러에 전달한다."""
    handler = AsyncMock()
    ns.register_external_handler(handler)
    task = NotificationQueueTask(
        ns,
        poll_interval=0,
        telegram_config=SimpleNamespace(
            enabled=True,
            route_levels={"STRATEGY": ["warning", "error", "critical"]},
        ),
        logger=MagicMock(),
    )

    event = await ns.emit(
        NotificationCategory.STRATEGY,
        NotificationLevel.INFO,
        "market timing",
        "telegram",
        metadata={"force_external": True},
    )

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    handler.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_drain_loop_skips_external_when_telegram_disabled(ns):
    handler = AsyncMock()
    ns.register_external_handler(handler)
    task = NotificationQueueTask(
        ns,
        poll_interval=0,
        telegram_config=SimpleNamespace(enabled=False, route_levels={}),
        logger=MagicMock(),
    )

    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.CRITICAL, "critical", "m")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_loop_no_handler_registered(ns):
    """외부 핸들러가 없어도 큐가 정상 소비된다."""
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "t", "m")

    await task.start()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    assert ns.external_handler_queue.qsize() == 0


# ── suspend / resume 동작 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suspend_accumulates_events_without_calling_handlers(ns):
    """SUSPENDED 상태에서 emit()된 이벤트는 큐에 쌓이고 핸들러는 호출되지 않는다."""
    handler = AsyncMock()
    ns.register_external_handler(handler)
    task = NotificationQueueTask(ns, poll_interval=0, logger=MagicMock())

    await task.start()
    await task.suspend()

    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "1", "m")
    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "2", "m")
    await ns.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "3", "m")

    # suspend 상태에서 yield 해도 핸들러는 호출되지 않아야 함
    await asyncio.sleep(0)
    handler.assert_not_awaited()
    assert ns.external_handler_queue.qsize() == 3

    # resume 후 적재된 3개 이벤트 모두 소비
    await task.resume()
    await asyncio.wait_for(ns.external_handler_queue.join(), timeout=2.0)
    await task.stop()

    assert handler.await_count == 3


@pytest.mark.asyncio
async def test_drain_loop_continues_when_queue_get_times_out(ns):
    logger = MagicMock()
    task = NotificationQueueTask(ns, poll_interval=0, logger=logger)
    task._resume_event = asyncio.Event()
    task._resume_event.set()

    with patch(
        "task.background.always_on.notification_queue_task.asyncio.wait_for",
        new=AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.CancelledError()]),
    ) as mock_wait_for:
        await task._drain_loop()

    assert mock_wait_for.await_count == 2
    logger.info.assert_called_with("NotificationQueueTask drain_loop 취소됨")


@pytest.mark.asyncio
async def test_drain_loop_logs_unexpected_exception_and_keeps_running(ns):
    logger = MagicMock()
    task = NotificationQueueTask(ns, poll_interval=0, logger=logger)
    task._resume_event = MagicMock()
    task._resume_event.wait = AsyncMock(
        side_effect=[RuntimeError("resume event broken"), asyncio.CancelledError()]
    )

    with patch(
        "task.background.always_on.notification_queue_task.asyncio.sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        await task._drain_loop()

    logger.error.assert_called_once()
    assert "[NotificationQueueTask] drain_loop" in logger.error.call_args.args[0]
    mock_sleep.assert_awaited_once_with(1.0)
