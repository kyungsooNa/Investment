import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskState
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
