"""
AfterMarketTask 기반 클래스 단위 테스트.

검증 범위:
  - 초기 상태: IDLE
  - _running_state: IDLE → RUNNING → IDLE 전환
  - _running_state: 중첩 호출 (_running_depth) — 가장 바깥 컨텍스트가 닫힐 때만 IDLE 복귀
  - _running_state: SUSPENDED/STOPPED 상태는 덮어쓰지 않음
  - _running_state: 예외 발생 시에도 IDLE 복귀
  - _after_market_scheduler: 루프 진입 시 IDLE 강제 전환
  - _after_market_scheduler: SUSPENDED/STOPPED 상태는 IDLE로 전환하지 않음
  - suspend/resume: IDLE 상태에서 noop
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskState, TaskPriority


# ---------------------------------------------------------------------------
# 테스트용 최소 구현체
# ---------------------------------------------------------------------------

class _ConcreteTask(AfterMarketTask):
    """AfterMarketTask 추상 클래스의 최소 구현체 (테스트 전용)."""

    @property
    def task_name(self) -> str:
        return "test_task"

    @property
    def _scheduler_label(self) -> str:
        return "test_task_scheduler"

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        pass

    async def start(self) -> None:
        pass

    def get_progress(self):
        return {"running": self._state == TaskState.RUNNING}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def task():
    return _ConcreteTask(mcs=MagicMock(), market_clock=MagicMock(), logger=MagicMock())


# ---------------------------------------------------------------------------
# 초기 상태
# ---------------------------------------------------------------------------

class TestInitialState:

    def test_initial_state_is_idle(self, task):
        assert task.state == TaskState.IDLE

    def test_running_depth_is_zero(self, task):
        assert task._running_depth == 0

    def test_tasks_list_is_empty(self, task):
        assert task._tasks == []

    def test_priority_is_low(self, task):
        assert task.priority == TaskPriority.LOW


# ---------------------------------------------------------------------------
# _running_state: 기본 IDLE ↔ RUNNING 전환
# ---------------------------------------------------------------------------

class TestRunningState:

    async def test_running_state_sets_running_during_execution(self, task):
        states_inside = []
        async with task._running_state():
            states_inside.append(task.state)
        assert states_inside == [TaskState.RUNNING]

    async def test_running_state_reverts_to_idle_after_exit(self, task):
        async with task._running_state():
            pass
        assert task.state == TaskState.IDLE

    async def test_running_state_reverts_to_idle_on_exception(self, task):
        with pytest.raises(ValueError):
            async with task._running_state():
                raise ValueError("test error")
        assert task.state == TaskState.IDLE

    async def test_running_depth_is_zero_after_exit(self, task):
        async with task._running_state():
            pass
        assert task._running_depth == 0

    # ── 중첩 호출 ─────────────────────────────────────────────────────────

    async def test_nested_running_state_stays_running_until_outermost_exits(self, task):
        """중첩 _running_state: 안쪽 컨텍스트 종료 후에도 RUNNING 유지."""
        state_after_inner_exit = None

        async with task._running_state():          # 외부 (depth=1)
            async with task._running_state():      # 내부 (depth=2)
                pass
            state_after_inner_exit = task.state    # 내부 종료 직후

        assert state_after_inner_exit == TaskState.RUNNING
        assert task.state == TaskState.IDLE

    async def test_nested_depth_counter_tracks_correctly(self, task):
        async with task._running_state():
            assert task._running_depth == 1
            async with task._running_state():
                assert task._running_depth == 2
            assert task._running_depth == 1
        assert task._running_depth == 0

    # ── SUSPENDED/STOPPED 보호 ────────────────────────────────────────────

    async def test_running_state_does_not_overwrite_suspended(self, task):
        """SUSPENDED 상태에서 _running_state 진입해도 상태를 덮어쓰지 않는다."""
        task._state = TaskState.SUSPENDED
        async with task._running_state():
            assert task.state == TaskState.SUSPENDED
        assert task.state == TaskState.SUSPENDED

    async def test_running_state_does_not_overwrite_stopped(self, task):
        """STOPPED 상태에서 _running_state 진입해도 상태를 덮어쓰지 않는다."""
        task._state = TaskState.STOPPED
        async with task._running_state():
            assert task.state == TaskState.STOPPED
        assert task.state == TaskState.STOPPED

    async def test_running_depth_not_incremented_when_suspended(self, task):
        task._state = TaskState.SUSPENDED
        async with task._running_state():
            assert task._running_depth == 0
        assert task._running_depth == 0

    async def test_running_depth_not_incremented_when_stopped(self, task):
        task._state = TaskState.STOPPED
        async with task._running_state():
            assert task._running_depth == 0
        assert task._running_depth == 0


# ---------------------------------------------------------------------------
# stop(): STOPPED 상태로 전환
# ---------------------------------------------------------------------------

class TestStop:

    async def test_stop_sets_stopped_state(self, task):
        await task.stop()
        assert task.state == TaskState.STOPPED

    async def test_stop_clears_tasks_list(self, task):
        mock_asyncio_task = MagicMock()
        mock_asyncio_task.done.return_value = False
        mock_asyncio_task.cancel = MagicMock()
        task._tasks.append(mock_asyncio_task)

        with patch("asyncio.gather", new_callable=AsyncMock):
            await task.stop()

        assert task._tasks == []

    async def test_stop_without_running_tasks_sets_stopped(self, task):
        await task.stop()
        assert task.state == TaskState.STOPPED
        assert task._tasks == []


# ---------------------------------------------------------------------------
# suspend / resume: IDLE 상태 noop 검증
# ---------------------------------------------------------------------------

class TestSuspendResumeIdle:

    async def test_suspend_when_idle_is_noop(self, task):
        """IDLE 상태에서 suspend() 호출 시 상태 변화 없음."""
        assert task.state == TaskState.IDLE
        await task.suspend()
        assert task.state == TaskState.IDLE

    async def test_resume_when_idle_is_noop(self, task):
        """IDLE 상태에서 resume() 호출 시 상태 변화 없음."""
        assert task.state == TaskState.IDLE
        await task.resume()
        assert task.state == TaskState.IDLE

    async def test_suspend_from_running_sets_suspended(self, task):
        task._state = TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED

    async def test_resume_from_suspended_sets_running(self, task):
        task._state = TaskState.SUSPENDED
        await task.resume()
        assert task.state == TaskState.RUNNING

    async def test_resume_when_stopped_is_noop(self, task):
        task._state = TaskState.STOPPED
        await task.resume()
        assert task.state == TaskState.STOPPED


# ---------------------------------------------------------------------------
# _after_market_scheduler: IDLE 강제 전환
# ---------------------------------------------------------------------------

class TestAfterMarketSchedulerIdleTransition:

    async def test_scheduler_sets_idle_on_entry(self, task):
        """_after_market_scheduler 진입 시 RUNNING 상태를 IDLE로 전환한다."""
        task._state = TaskState.RUNNING

        with patch(
            "task.background.after_market.after_market_task_base.run_after_market_loop",
            new_callable=AsyncMock,
        ):
            await task._after_market_scheduler()

        assert task.state == TaskState.IDLE

    async def test_scheduler_does_not_set_idle_when_suspended(self, task):
        """SUSPENDED 상태에서 _after_market_scheduler 진입해도 IDLE로 전환하지 않는다."""
        task._state = TaskState.SUSPENDED

        with patch(
            "task.background.after_market.after_market_task_base.run_after_market_loop",
            new_callable=AsyncMock,
        ):
            await task._after_market_scheduler()

        assert task.state == TaskState.SUSPENDED

    async def test_scheduler_does_not_set_idle_when_stopped(self, task):
        """STOPPED 상태에서 _after_market_scheduler 진입해도 IDLE로 전환하지 않는다."""
        task._state = TaskState.STOPPED

        with patch(
            "task.background.after_market.after_market_task_base.run_after_market_loop",
            new_callable=AsyncMock,
        ):
            await task._after_market_scheduler()

        assert task.state == TaskState.STOPPED

    async def test_scheduler_calls_on_market_closed_via_running_state(self, task):
        """스케줄러가 _on_market_closed를 _running_state 내에서 호출한다."""
        called_states = []

        async def mock_on_closed(date: str):
            called_states.append(task.state)

        task._on_market_closed = mock_on_closed

        async def fake_loop(mcs, market_clock, logger, on_market_closed, label=None, delay_sec=0):
            await on_market_closed("20260409")

        with patch(
            "task.background.after_market.after_market_task_base.run_after_market_loop",
            side_effect=fake_loop,
        ):
            await task._after_market_scheduler()

        assert called_states == [TaskState.RUNNING]
        assert task.state == TaskState.IDLE
