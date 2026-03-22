"""
BackgroundScheduler 단위 테스트.
태스크 등록, start_all, shutdown, suspend_all, resume_all 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from scheduler.background_scheduler import BackgroundScheduler
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


def _make_mock_task(name: str, state: TaskState = TaskState.IDLE, priority: TaskPriority = TaskPriority.LOW):
    """SchedulableTask mock 생성."""
    task = MagicMock(spec=SchedulableTask)
    task.task_name = name
    task.priority = priority
    task.state = state
    task.start = AsyncMock()
    task.stop = AsyncMock()
    task.suspend = AsyncMock()
    task.resume = AsyncMock()
    return task


@pytest.fixture
def scheduler():
    return BackgroundScheduler(logger=MagicMock())


def test_register(scheduler):
    task = _make_mock_task("test_task")
    scheduler.register(task)
    assert scheduler.get_task("test_task") is task


def test_register_duplicate_overwrites(scheduler):
    task1 = _make_mock_task("dup")
    task2 = _make_mock_task("dup")
    scheduler.register(task1)
    scheduler.register(task2)
    assert scheduler.get_task("dup") is task2


def test_unregister(scheduler):
    task = _make_mock_task("removable")
    scheduler.register(task)
    scheduler.unregister("removable")
    assert scheduler.get_task("removable") is None


@pytest.mark.asyncio
async def test_start_all(scheduler):
    t1 = _make_mock_task("t1", TaskState.IDLE)
    t2 = _make_mock_task("t2", TaskState.STOPPED)
    t3 = _make_mock_task("t3", TaskState.RUNNING)  # 이미 실행 중 → 스킵
    scheduler.register(t1)
    scheduler.register(t2)
    scheduler.register(t3)

    await scheduler.start_all()

    t1.start.assert_awaited_once()
    t2.start.assert_awaited_once()
    t3.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown(scheduler):
    t1 = _make_mock_task("t1", TaskState.RUNNING)
    t2 = _make_mock_task("t2", TaskState.SUSPENDED)
    t3 = _make_mock_task("t3", TaskState.IDLE)  # idle → 스킵
    scheduler.register(t1)
    scheduler.register(t2)
    scheduler.register(t3)

    await scheduler.shutdown()

    t1.stop.assert_awaited_once()
    t2.stop.assert_awaited_once()
    t3.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_suspend_all(scheduler):
    t1 = _make_mock_task("t1", TaskState.RUNNING)
    t2 = _make_mock_task("t2", TaskState.IDLE)  # idle → 스킵
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.suspend_all()

    t1.suspend.assert_awaited_once()
    t2.suspend.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_all(scheduler):
    t1 = _make_mock_task("t1", TaskState.SUSPENDED)
    t2 = _make_mock_task("t2", TaskState.RUNNING)  # running → 스킵
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.resume_all()

    t1.resume.assert_awaited_once()
    t2.resume.assert_not_awaited()


def test_get_all_status(scheduler):
    t1 = _make_mock_task("ranking", TaskState.RUNNING, TaskPriority.LOW)
    t2 = _make_mock_task("watchdog", TaskState.IDLE, TaskPriority.NORMAL)
    scheduler.register(t1)
    scheduler.register(t2)

    status = scheduler.get_all_status()
    assert len(status) == 2
    assert status[0] == {"name": "ranking", "state": "running", "priority": 100}
    assert status[1] == {"name": "watchdog", "state": "idle", "priority": 50}


@pytest.mark.asyncio
async def test_start_all_handles_exception(scheduler):
    """start 중 예외가 발생해도 다른 태스크는 계속 시작된다."""
    t1 = _make_mock_task("fail_task", TaskState.IDLE)
    t1.start = AsyncMock(side_effect=Exception("boom"))
    t2 = _make_mock_task("ok_task", TaskState.IDLE)
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.start_all()

    t1.start.assert_awaited_once()
    t2.start.assert_awaited_once()
