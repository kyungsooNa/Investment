"""
BackgroundScheduler 단위 테스트.
태스크 등록, start_all, shutdown, suspend_all, resume_all 검증.
"""
import pytest
import asyncio
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
    t3 = _make_mock_task("t3", TaskState.RUNNING)    # 이미 실행 중 → 스킵
    t4 = _make_mock_task("t4", TaskState.SUSPENDED)  # 일시정지 → resume() 호출
    scheduler.register(t1)
    scheduler.register(t2)
    scheduler.register(t3)
    scheduler.register(t4)

    await scheduler.start_all()

    t1.start.assert_awaited_once()
    t2.start.assert_awaited_once()
    t3.start.assert_not_awaited()
    t4.resume.assert_awaited_once()   # SUSPENDED → resume()
    t4.start.assert_not_awaited()     # start()는 호출 안 됨


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
async def test_shutdown_stops_idle_task_with_active_background_loop(scheduler):
    """상태는 IDLE이어도 내부 백그라운드 루프가 살아 있으면 shutdown에서 stop한다."""
    t1 = _make_mock_task("idle_loop", TaskState.IDLE)
    pending = asyncio.create_task(asyncio.sleep(60))
    t1._tasks = [pending]
    scheduler.register(t1)

    try:
        await scheduler.shutdown()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    t1.stop.assert_awaited_once()


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


def test_unregister_nonexistent(scheduler):
    """존재하지 않는 태스크 제거는 아무 효과도 없다."""
    scheduler.unregister("ghost")  # 예외 없이 통과해야 함
    assert scheduler.get_task("ghost") is None


@pytest.mark.asyncio
async def test_start_all_with_worker_pool_and_time_dispatcher():
    """worker_pool / time_dispatcher 주입 시 start_all에서 함께 시작된다."""
    mock_worker_pool = MagicMock()
    mock_worker_pool.start = AsyncMock()
    mock_time_dispatcher = MagicMock()
    mock_time_dispatcher.run = AsyncMock(return_value=None)

    scheduler = BackgroundScheduler(
        logger=MagicMock(),
        worker_pool=mock_worker_pool,
        time_dispatcher=mock_time_dispatcher,
    )
    task = _make_mock_task("t1", TaskState.IDLE)
    scheduler.register(task)

    await scheduler.start_all()

    mock_worker_pool.start.assert_awaited_once()
    assert len(scheduler._infra_tasks) == 1
    task.start.assert_awaited_once()

    await scheduler.start_all()
    mock_worker_pool.start.assert_awaited_once()
    assert len(scheduler._infra_tasks) == 1

    # 정리
    scheduler._infra_tasks[0].cancel()
    import asyncio
    await asyncio.gather(*scheduler._infra_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_with_worker_pool_and_time_dispatcher():
    """shutdown 시 time_dispatcher 중지 → 태스크 종료 → worker_pool.shutdown 순서."""
    mock_worker_pool = MagicMock()
    mock_worker_pool.start = AsyncMock()
    mock_worker_pool.shutdown = AsyncMock()
    mock_time_dispatcher = MagicMock()
    mock_time_dispatcher.run = AsyncMock(return_value=None)
    mock_time_dispatcher.stop = MagicMock()

    scheduler = BackgroundScheduler(
        logger=MagicMock(),
        worker_pool=mock_worker_pool,
        time_dispatcher=mock_time_dispatcher,
    )
    task = _make_mock_task("t1", TaskState.RUNNING)
    scheduler.register(task)

    await scheduler.start_all()
    await scheduler.shutdown()

    mock_time_dispatcher.stop.assert_called_once()
    task.stop.assert_awaited_once()
    mock_worker_pool.shutdown.assert_awaited_once()
    assert scheduler._infra_tasks == []


@pytest.mark.asyncio
async def test_suspend_all_with_worker_pool():
    """suspend_all 시 worker_pool.suspend()도 호출된다."""
    mock_worker_pool = MagicMock()
    mock_worker_pool.suspend = MagicMock()

    scheduler = BackgroundScheduler(logger=MagicMock(), worker_pool=mock_worker_pool)
    task = _make_mock_task("t1", TaskState.RUNNING)
    scheduler.register(task)

    await scheduler.suspend_all()

    mock_worker_pool.suspend.assert_called_once()
    task.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_all_with_worker_pool():
    """resume_all 시 worker_pool.resume()도 호출된다."""
    mock_worker_pool = MagicMock()
    mock_worker_pool.resume = MagicMock()

    scheduler = BackgroundScheduler(logger=MagicMock(), worker_pool=mock_worker_pool)
    task = _make_mock_task("t1", TaskState.SUSPENDED)
    scheduler.register(task)

    await scheduler.resume_all()

    mock_worker_pool.resume.assert_called_once()
    task.resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_all_resume_exception(scheduler):
    """SUSPENDED 태스크의 resume 중 예외가 발생해도 다른 태스크는 계속 시작된다."""
    t1 = _make_mock_task("fail_resume", TaskState.SUSPENDED)
    t1.resume = AsyncMock(side_effect=Exception("resume boom"))
    t2 = _make_mock_task("ok_task", TaskState.IDLE)
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.start_all()

    t1.resume.assert_awaited_once()
    t2.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_handles_exception(scheduler):
    """stop 중 예외가 발생해도 다른 태스크는 계속 종료된다."""
    t1 = _make_mock_task("fail_stop", TaskState.RUNNING)
    t1.stop = AsyncMock(side_effect=Exception("stop boom"))
    t2 = _make_mock_task("ok_stop", TaskState.RUNNING)
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.shutdown()

    t1.stop.assert_awaited_once()
    t2.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_suspend_all_handles_exception(scheduler):
    """suspend 중 예외가 발생해도 다른 태스크는 계속 처리된다."""
    t1 = _make_mock_task("fail_suspend", TaskState.RUNNING)
    t1.suspend = AsyncMock(side_effect=Exception("suspend boom"))
    t2 = _make_mock_task("ok_suspend", TaskState.RUNNING)
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.suspend_all()

    t1.suspend.assert_awaited_once()
    t2.suspend.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_all_handles_exception(scheduler):
    """resume 중 예외가 발생해도 다른 태스크는 계속 재개된다."""
    t1 = _make_mock_task("fail_resume", TaskState.SUSPENDED)
    t1.resume = AsyncMock(side_effect=Exception("resume boom"))
    t2 = _make_mock_task("ok_resume", TaskState.SUSPENDED)
    scheduler.register(t1)
    scheduler.register(t2)

    await scheduler.resume_all()

    t1.resume.assert_awaited_once()
    t2.resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_all_concurrent_call_is_serialized(scheduler):
    t1 = _make_mock_task("slow", TaskState.IDLE)
    scheduler.register(t1)
    scheduler._starting = True

    await scheduler.start_all()

    t1.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_all_concurrent_calls_start_tasks_once(scheduler):
    """실제 동시 start_all 호출에서도 첫 호출만 태스크를 시작한다."""
    started = asyncio.Event()
    release = asyncio.Event()
    t1 = _make_mock_task("slow", TaskState.IDLE)

    async def slow_start():
        started.set()
        await release.wait()

    t1.start = AsyncMock(side_effect=slow_start)
    scheduler.register(t1)

    first = asyncio.create_task(scheduler.start_all())
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(scheduler.start_all())
    release.set()

    await asyncio.gather(first, second)

    t1.start.assert_awaited_once()
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] start_all 중복 호출 무시")


@pytest.mark.asyncio
async def test_start_all_sequential_repeat_is_noop(scheduler):
    """웹 lifespan/수동 start 연타처럼 시작 완료 후 다시 눌러도 재시작하지 않는다."""
    t1 = _make_mock_task("manual_start", TaskState.IDLE)
    scheduler.register(t1)

    await scheduler.start_all()
    await scheduler.start_all()

    t1.start.assert_awaited_once()
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] 이미 시작됨 — start_all 호출 무시")


@pytest.mark.asyncio
async def test_shutdown_concurrent_call_is_serialized(scheduler):
    t1 = _make_mock_task("slow", TaskState.RUNNING)
    scheduler.register(t1)
    scheduler._shutting_down = True

    await scheduler.shutdown()

    t1.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_concurrent_calls_stop_tasks_once(scheduler):
    """실제 동시 shutdown 호출에서도 첫 호출만 태스크를 종료한다."""
    started = asyncio.Event()
    release = asyncio.Event()
    t1 = _make_mock_task("slow_stop", TaskState.RUNNING)

    async def slow_stop():
        started.set()
        await release.wait()

    t1.stop = AsyncMock(side_effect=slow_stop)
    scheduler.register(t1)

    first = asyncio.create_task(scheduler.shutdown())
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(scheduler.shutdown())
    release.set()

    await asyncio.gather(first, second)

    t1.stop.assert_awaited_once()
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] shutdown 중복 호출 무시")


@pytest.mark.asyncio
async def test_shutdown_sequential_repeat_is_noop(scheduler):
    """종료 완료 후 shutdown 재호출은 stop을 반복하지 않는다."""
    t1 = _make_mock_task("manual_shutdown", TaskState.RUNNING)
    scheduler.register(t1)

    await scheduler.shutdown()
    await scheduler.shutdown()

    t1.stop.assert_awaited_once()
    scheduler._logger.warning.assert_any_call("[BackgroundScheduler] 이미 종료됨 — shutdown 호출 무시")
