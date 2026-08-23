import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.paper_account_expiry_alert_task import (
    PaperAccountExpiryAlertTask,
)


@pytest.mark.asyncio
async def test_task_runs_once_per_day():
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 11, 12, 8, 30)
    service = AsyncMock()
    service.check_and_notify.return_value = {"alerted": True, "days_left": 7}
    task = PaperAccountExpiryAlertTask(
        alert_service=service,
        env=MagicMock(is_paper_trading=True, paper_stock_account_number="50202454"),
        market_clock=clock,
        logger=MagicMock(),
    )

    first = await task.run_once()
    second = await task.run_once()

    assert first == {"alerted": True, "days_left": 7}
    assert second == {"alerted": False, "reason": "already_checked_today"}
    service.check_and_notify.assert_awaited_once_with(True, "50202454")


@pytest.mark.asyncio
async def test_task_lifecycle_cancels_background_loop():
    task = PaperAccountExpiryAlertTask(
        alert_service=AsyncMock(),
        env=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )

    try:
        await task.start()
        await task.start()
        assert len(task._tasks) == 1
        assert task.state == TaskState.IDLE
    finally:
        await task.stop()

    assert task.state == TaskState.STOPPED
    assert task._tasks == []


@pytest.mark.asyncio
async def test_loop_runs_when_due_and_handles_errors():
    task = PaperAccountExpiryAlertTask(
        alert_service=AsyncMock(),
        env=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )
    task._should_run_now = AsyncMock(side_effect=[True, RuntimeError("boom"), asyncio.CancelledError()])
    task.run_once = AsyncMock()

    with patch("task.background.intraday.paper_account_expiry_alert_task.asyncio.sleep", new_callable=AsyncMock):
        await task._loop()

    task.run_once.assert_awaited_once()
    task._logger.error.assert_called_once()


def _task(*, market_clock=None, env=None, alert_service=None):
    return PaperAccountExpiryAlertTask(
        alert_service=alert_service or AsyncMock(),
        env=env if env is not None else MagicMock(),
        market_clock=market_clock,
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_run_once_reports_missing_market_clock():
    task = _task(market_clock=None)

    assert await task.run_once() == {"alerted": False, "reason": "market_clock_missing"}


@pytest.mark.asyncio
async def test_should_run_now_requires_market_clock_and_new_date():
    task = _task(market_clock=None)
    assert await task._should_run_now() is False

    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 11, 12, 8, 30)
    task._market_clock = clock
    assert await task._should_run_now() is True

    task._last_checked_date = "20261112"
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_run_once_defaults_env_attributes_when_absent():
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 11, 12, 8, 30)
    service = AsyncMock()
    service.check_and_notify.return_value = {"alerted": False, "reason": "real_mode"}
    env = MagicMock(spec=[])  # is_paper_trading / paper_stock_account_number 없음
    task = _task(market_clock=clock, env=env, alert_service=service)

    result = await task.run_once()

    service.check_and_notify.assert_awaited_once_with(False, "")
    assert result == {"alerted": False, "reason": "real_mode"}
    assert task.get_progress()["last_checked_date"] == "20261112"
    assert task.get_progress()["last_result"] == result


def test_task_identity_and_initial_progress():
    task = _task(market_clock=MagicMock())

    assert task.task_name == "paper_account_expiry_alert"
    assert task.priority == TaskPriority.NORMAL
    assert task.get_progress() == {
        "running": False,
        "last_checked_date": None,
        "last_result": {"alerted": False, "reason": "never_checked"},
    }


@pytest.mark.asyncio
async def test_suspend_only_applies_while_running_and_resume_restores_idle():
    task = _task(market_clock=MagicMock())

    await task.suspend()
    assert task.state == TaskState.IDLE

    task._state = TaskState.RUNNING
    await task.suspend()
    assert task.state == TaskState.SUSPENDED

    await task.resume()
    assert task.state == TaskState.IDLE

    await task.resume()
    assert task.state == TaskState.IDLE


@pytest.mark.asyncio
async def test_restart_after_stop_resets_state_to_idle():
    task = _task(market_clock=MagicMock())

    await task.stop()
    assert task.state == TaskState.STOPPED

    await task.start()
    assert task.state == TaskState.IDLE

    await task.stop()


@pytest.mark.asyncio
async def test_loop_skips_run_while_suspended():
    task = _task(market_clock=MagicMock())
    task._should_run_now = AsyncMock()
    task.run_once = AsyncMock()
    task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch(
        "task.background.intraday.paper_account_expiry_alert_task.asyncio.sleep", sleep_mock
    ):
        await task._loop()

    task._should_run_now.assert_not_awaited()
    task.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_does_not_run_when_not_due():
    task = _task(market_clock=MagicMock())
    task._should_run_now = AsyncMock(side_effect=[False, asyncio.CancelledError()])
    task.run_once = AsyncMock()

    with patch(
        "task.background.intraday.paper_account_expiry_alert_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    task.run_once.assert_not_awaited()
