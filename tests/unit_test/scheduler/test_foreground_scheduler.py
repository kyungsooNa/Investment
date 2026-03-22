"""
ForegroundScheduler 단위 테스트.
Reference counting 기반 suspend/resume 조율 검증.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from scheduler.foreground_scheduler import ForegroundScheduler
from scheduler.background_scheduler import BackgroundScheduler


@pytest.fixture
def bg_scheduler():
    bg = MagicMock(spec=BackgroundScheduler)
    bg.suspend_all = AsyncMock()
    bg.resume_all = AsyncMock()
    return bg


@pytest.fixture
def fg_scheduler(bg_scheduler):
    return ForegroundScheduler(background_scheduler=bg_scheduler, logger=MagicMock())


async def test_execute_returns_result(fg_scheduler):
    """execute()가 코루틴의 반환값을 그대로 돌려준다."""
    async def my_coro():
        return 42

    result = await fg_scheduler.execute(my_coro())
    assert result == 42


async def test_single_execute_suspends_and_resumes(fg_scheduler, bg_scheduler):
    """단일 execute() 호출 시 suspend → coro 실행 → resume 순서."""
    async def my_coro():
        return "ok"

    await fg_scheduler.execute(my_coro())

    bg_scheduler.suspend_all.assert_awaited_once()
    bg_scheduler.resume_all.assert_awaited_once()


async def test_active_count_during_execute(fg_scheduler):
    """execute() 실행 중 active_count == 1, 완료 후 0."""
    captured_count = None

    async def my_coro():
        nonlocal captured_count
        captured_count = fg_scheduler.active_count
        return "done"

    await fg_scheduler.execute(my_coro())
    assert captured_count == 1
    assert fg_scheduler.active_count == 0
    assert fg_scheduler.is_active is False


async def test_concurrent_execute_suspends_once(fg_scheduler, bg_scheduler):
    """동시 실행 시 suspend는 첫 번째만, resume은 마지막만 호출된다."""
    entered = asyncio.Event()
    barrier = asyncio.Event()

    async def slow_coro(val):
        entered.set()
        await barrier.wait()
        return val

    # 첫 번째 foreground action 시작 (barrier에서 대기)
    task1 = asyncio.create_task(fg_scheduler.execute(slow_coro(1)))
    await entered.wait()  # task1이 coro 내부에 진입할 때까지 대기

    assert fg_scheduler.active_count == 1
    bg_scheduler.suspend_all.assert_awaited_once()

    # 두 번째 foreground action 시작
    entered.clear()
    task2 = asyncio.create_task(fg_scheduler.execute(slow_coro(2)))
    await entered.wait()  # task2도 coro 내부에 진입

    assert fg_scheduler.active_count == 2
    # suspend는 처음 한 번만 (count가 1→2일 때는 호출 안 함)
    assert bg_scheduler.suspend_all.await_count == 1

    # barrier 해제 → 둘 다 완료
    barrier.set()
    await asyncio.gather(task1, task2)

    assert fg_scheduler.active_count == 0
    # resume도 마지막에 한 번만 (count가 2→1일 때는 호출 안 함)
    assert bg_scheduler.resume_all.await_count == 1


async def test_execute_resumes_on_exception(fg_scheduler, bg_scheduler):
    """코루틴에서 예외가 발생해도 resume이 호출된다."""
    async def failing_coro():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await fg_scheduler.execute(failing_coro())

    bg_scheduler.suspend_all.assert_awaited_once()
    bg_scheduler.resume_all.assert_awaited_once()
    assert fg_scheduler.active_count == 0


async def test_is_active_property(fg_scheduler):
    """초기 상태에서 is_active == False."""
    assert fg_scheduler.is_active is False
    assert fg_scheduler.active_count == 0


# --- context() 컨텍스트 매니저 테스트 ---


async def test_context_manager_basic(fg_scheduler, bg_scheduler):
    """context() 진입 시 suspend, 퇴장 시 resume."""
    async with fg_scheduler.context():
        assert fg_scheduler.active_count == 1
        bg_scheduler.suspend_all.assert_awaited_once()

    assert fg_scheduler.active_count == 0
    bg_scheduler.resume_all.assert_awaited_once()


async def test_context_manager_exception(fg_scheduler, bg_scheduler):
    """context() 내부 예외 발생 시에도 resume이 호출된다."""
    with pytest.raises(RuntimeError, match="test error"):
        async with fg_scheduler.context():
            raise RuntimeError("test error")

    bg_scheduler.suspend_all.assert_awaited_once()
    bg_scheduler.resume_all.assert_awaited_once()
    assert fg_scheduler.active_count == 0


async def test_context_manager_nested(fg_scheduler, bg_scheduler):
    """중첩 context() 사용 시 suspend/resume은 각 1회만."""
    entered = asyncio.Event()
    barrier = asyncio.Event()

    async def worker():
        async with fg_scheduler.context():
            entered.set()
            await barrier.wait()

    task = asyncio.create_task(worker())
    await entered.wait()

    # 두 번째 context 진입
    async with fg_scheduler.context():
        assert fg_scheduler.active_count == 2
        assert bg_scheduler.suspend_all.await_count == 1

    # 첫 번째 worker 아직 실행 중 → resume 안 됨
    assert fg_scheduler.active_count == 1
    assert bg_scheduler.resume_all.await_count == 0

    barrier.set()
    await task

    assert fg_scheduler.active_count == 0
    assert bg_scheduler.resume_all.await_count == 1
