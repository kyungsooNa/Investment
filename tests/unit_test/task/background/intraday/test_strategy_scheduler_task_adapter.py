"""
StrategySchedulerTaskAdapter 단위 테스트.
StrategyScheduler를 SchedulableTask 인터페이스로 래핑하는 어댑터 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock
from task.background.intraday.strategy_scheduler_task_adapter import StrategySchedulerTaskAdapter
from interfaces.schedulable_task import TaskPriority, TaskState


@pytest.fixture
def mock_strategy_scheduler():
    scheduler = MagicMock()
    scheduler.restore_state = AsyncMock()
    scheduler.stop = AsyncMock()
    scheduler._running = False
    return scheduler


@pytest.fixture
def adapter(mock_strategy_scheduler):
    return StrategySchedulerTaskAdapter(mock_strategy_scheduler)


@pytest.fixture
def mock_market_clock():
    clock = MagicMock()
    clock.get_sleep_seconds_until_market_close.return_value = 1800
    return clock


def test_task_name(adapter):
    assert adapter.task_name == "strategy_scheduler"


def test_market_scoped_task_name(mock_strategy_scheduler):
    adapter = StrategySchedulerTaskAdapter(mock_strategy_scheduler, market="domestic")

    assert adapter.task_name == "domestic_strategy_scheduler"


def test_priority(adapter):
    assert adapter.priority == TaskPriority.NORMAL


def test_initial_state(adapter):
    assert adapter.state == TaskState.IDLE


async def test_start(adapter, mock_strategy_scheduler):
    """start() 호출 시 restore_state가 호출되고 상태가 RUNNING으로 변경된다."""
    await adapter.start()

    mock_strategy_scheduler.restore_state.assert_awaited_once()
    assert adapter.state == TaskState.RUNNING


async def test_start_already_running(adapter, mock_strategy_scheduler):
    """이미 RUNNING 상태이면 start()가 아무것도 하지 않는다."""
    await adapter.start()
    mock_strategy_scheduler.restore_state.reset_mock()

    await adapter.start()

    mock_strategy_scheduler.restore_state.assert_not_awaited()
    assert adapter.state == TaskState.RUNNING


async def test_stop_when_running(adapter, mock_strategy_scheduler):
    """전략 스케줄러가 실행 중이면 stop(save_state=True)을 호출한다."""
    mock_strategy_scheduler._running = True

    await adapter.stop()

    mock_strategy_scheduler.stop.assert_awaited_once_with(save_state=True)
    assert adapter.state == TaskState.STOPPED


async def test_stop_when_not_running(adapter, mock_strategy_scheduler):
    """전략 스케줄러가 실행 중이 아니면 stop()을 호출하지 않는다."""
    mock_strategy_scheduler._running = False

    await adapter.stop()

    mock_strategy_scheduler.stop.assert_not_awaited()
    assert adapter.state == TaskState.STOPPED


async def test_suspend(adapter):
    """RUNNING 상태에서 suspend() 호출 시 SUSPENDED로 변경된다."""
    await adapter.start()
    await adapter.suspend()
    assert adapter.state == TaskState.SUSPENDED


async def test_suspend_not_running(adapter):
    """RUNNING이 아닌 상태에서 suspend()는 상태를 변경하지 않는다."""
    assert adapter.state == TaskState.IDLE
    await adapter.suspend()
    assert adapter.state == TaskState.IDLE


async def test_resume(adapter):
    """SUSPENDED 상태에서 resume() 호출 시 RUNNING으로 변경된다."""
    await adapter.start()
    await adapter.suspend()
    await adapter.resume()
    assert adapter.state == TaskState.RUNNING


async def test_resume_not_suspended(adapter):
    """SUSPENDED가 아닌 상태에서 resume()은 상태를 변경하지 않는다."""
    await adapter.start()
    assert adapter.state == TaskState.RUNNING
    await adapter.resume()
    assert adapter.state == TaskState.RUNNING


async def test_state_running_during_market_hours(mock_strategy_scheduler, mock_market_clock):
    """장 시간대면 RUNNING 상태를 그대로 반환한다."""
    adapter = StrategySchedulerTaskAdapter(mock_strategy_scheduler, market_clock=mock_market_clock)

    await adapter.start()

    assert adapter.state == TaskState.RUNNING
    mock_market_clock.get_sleep_seconds_until_market_close.assert_called_once()


@pytest.mark.parametrize("seconds_until_close", [3601, 0, -1])
async def test_state_running_outside_market_hours_returns_idle(
    mock_strategy_scheduler,
    mock_market_clock,
    seconds_until_close,
):
    """장 시간대가 아니면 RUNNING 상태라도 IDLE로 표시한다."""
    mock_market_clock.get_sleep_seconds_until_market_close.return_value = seconds_until_close
    adapter = StrategySchedulerTaskAdapter(mock_strategy_scheduler, market_clock=mock_market_clock)

    await adapter.start()

    assert adapter.state == TaskState.IDLE


async def test_state_running_with_non_numeric_market_clock_value_keeps_running(
    mock_strategy_scheduler,
    mock_market_clock,
):
    """market_clock 반환값이 숫자가 아니면 상태를 변경하지 않는다."""
    mock_market_clock.get_sleep_seconds_until_market_close.return_value = None
    adapter = StrategySchedulerTaskAdapter(mock_strategy_scheduler, market_clock=mock_market_clock)

    await adapter.start()

    assert adapter.state == TaskState.RUNNING


async def test_state_not_running_does_not_query_market_clock(
    mock_strategy_scheduler,
    mock_market_clock,
):
    """RUNNING이 아닌 상태에서는 market_clock을 조회하지 않는다."""
    adapter = StrategySchedulerTaskAdapter(mock_strategy_scheduler, market_clock=mock_market_clock)

    assert adapter.state == TaskState.IDLE
    mock_market_clock.get_sleep_seconds_until_market_close.assert_not_called()


async def test_full_lifecycle(adapter, mock_strategy_scheduler):
    """전체 라이프사이클: IDLE → start → suspend → resume → stop."""
    mock_strategy_scheduler._running = True

    assert adapter.state == TaskState.IDLE

    await adapter.start()
    assert adapter.state == TaskState.RUNNING

    await adapter.suspend()
    assert adapter.state == TaskState.SUSPENDED

    await adapter.resume()
    assert adapter.state == TaskState.RUNNING

    await adapter.stop()
    assert adapter.state == TaskState.STOPPED


# ── get_progress() 테스트 ────────────────────────────────────────────────────


def test_get_progress_initial_state(adapter, mock_strategy_scheduler):
    """초기 상태: running=False, active/total 전략 수 = 0."""
    mock_strategy_scheduler.get_status.return_value = {"strategies": []}

    p = adapter.get_progress()

    assert p["running"] is False
    assert p["active_strategies"] == 0
    assert p["total_strategies"] == 0


async def test_get_progress_running_state(adapter, mock_strategy_scheduler):
    """RUNNING 상태이면 running=True."""
    mock_strategy_scheduler.get_status.return_value = {"strategies": []}

    await adapter.start()
    p = adapter.get_progress()

    assert p["running"] is True


def test_get_progress_counts_active_and_total_strategies(adapter, mock_strategy_scheduler):
    """활성/비활성 전략 수가 올바르게 집계된다."""
    mock_strategy_scheduler.get_status.return_value = {
        "strategies": [
            {"name": "momentum", "enabled": True},
            {"name": "gap_up", "enabled": True},
            {"name": "volume_breakout", "enabled": False},
        ]
    }

    p = adapter.get_progress()

    assert p["total_strategies"] == 3
    assert p["active_strategies"] == 2


def test_get_progress_all_strategies_active(adapter, mock_strategy_scheduler):
    """모든 전략이 활성화된 경우."""
    mock_strategy_scheduler.get_status.return_value = {
        "strategies": [
            {"name": "strat_a", "enabled": True},
            {"name": "strat_b", "enabled": True},
        ]
    }

    p = adapter.get_progress()

    assert p["active_strategies"] == 2
    assert p["total_strategies"] == 2


def test_get_progress_no_active_strategies(adapter, mock_strategy_scheduler):
    """활성화된 전략이 없는 경우."""
    mock_strategy_scheduler.get_status.return_value = {
        "strategies": [
            {"name": "strat_a", "enabled": False},
        ]
    }

    p = adapter.get_progress()

    assert p["active_strategies"] == 0
    assert p["total_strategies"] == 1


def test_get_progress_scheduler_exception_returns_zeros(adapter, mock_strategy_scheduler):
    """get_status() 예외 발생 시 active/total이 0으로 안전하게 반환된다."""
    mock_strategy_scheduler.get_status.side_effect = RuntimeError("스케줄러 오류")

    p = adapter.get_progress()

    assert p["active_strategies"] == 0
    assert p["total_strategies"] == 0
