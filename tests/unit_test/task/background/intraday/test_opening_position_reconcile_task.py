import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskState
from task.background.intraday.opening_position_reconcile_task import OpeningPositionReconcileTask


@pytest.mark.asyncio
async def test_opening_position_reconcile_runs_once_in_opening_window():
    clock = MagicMock()
    open_time = datetime(2026, 4, 30, 9, 0)
    clock.get_market_open_time.return_value = open_time
    clock.get_current_kst_time.return_value = open_time + timedelta(seconds=61)
    mcs = AsyncMock()
    mcs.is_business_day.return_value = True
    service = AsyncMock()
    service.reconcile_once.return_value = {"mismatch_count": 1, "executed": [], "skipped": []}
    task = OpeningPositionReconcileTask(
        reconcile_service=service,
        market_calendar_service=mcs,
        market_clock=clock,
        notification_service=AsyncMock(),
        logger=MagicMock(),
    )

    assert await task._should_run_now() is True
    result = await task.run_once()

    service.reconcile_once.assert_awaited_once()
    assert result["mismatch_count"] == 1
    assert task._last_checked_date == "20260430"
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_opening_position_reconcile_does_not_stamp_date_on_error_result():
    clock = MagicMock()
    open_time = datetime(2026, 4, 30, 9, 0)
    clock.get_market_open_time.return_value = open_time
    clock.get_current_kst_time.return_value = open_time + timedelta(seconds=61)
    mcs = AsyncMock()
    mcs.is_business_day.return_value = True
    service = AsyncMock()
    service.reconcile_once.return_value = {"mismatch_count": 0, "error": "denied"}
    task = OpeningPositionReconcileTask(
        reconcile_service=service,
        market_calendar_service=mcs,
        market_clock=clock,
        notification_service=AsyncMock(),
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result["error"] == "denied"
    assert task._last_checked_date is None
    assert await task._should_run_now() is True


@pytest.mark.asyncio
async def test_opening_position_reconcile_skips_before_delay_and_after_window():
    clock = MagicMock()
    open_time = datetime(2026, 4, 30, 9, 0)
    clock.get_market_open_time.return_value = open_time
    mcs = AsyncMock()
    mcs.is_business_day.return_value = True
    task = OpeningPositionReconcileTask(
        reconcile_service=AsyncMock(),
        market_calendar_service=mcs,
        market_clock=clock,
        open_delay_sec=60,
        run_window_min=10,
    )

    clock.get_current_kst_time.return_value = open_time + timedelta(seconds=30)
    assert await task._should_run_now() is False

    clock.get_current_kst_time.return_value = open_time + timedelta(minutes=12)
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_opening_position_reconcile_notifies_mismatch_and_failure():
    ns = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.return_value = {"mismatch_count": 2, "executed": [], "skipped": []}
    task = OpeningPositionReconcileTask(
        reconcile_service=service,
        notification_service=ns,
        logger=MagicMock(),
    )

    result = await task.run_once()

    assert result["mismatch_count"] == 2
    ns.emit.assert_awaited_once()

    ns.reset_mock()
    service.reconcile_once.side_effect = RuntimeError("boom")
    result = await task.run_once()

    assert result["error"] == "boom"
    ns.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_opening_position_reconcile_lifecycle_stops_background_loop():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())

    try:
        await task.start()
        assert task.state == TaskState.IDLE
        await task.start()
        assert len(task._tasks) == 1
    finally:
        await task.stop()

    assert task.state == TaskState.STOPPED
    assert task._tasks == []


@pytest.mark.asyncio
async def test_opening_position_reconcile_loop_runs_once_and_recovers_errors():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())
    task._should_run_now = AsyncMock(side_effect=[True, RuntimeError("loop boom"), asyncio.CancelledError()])
    task.run_once = AsyncMock()

    with patch("task.background.intraday.opening_position_reconcile_task.asyncio.sleep", new_callable=AsyncMock):
        await task._loop()

    task.run_once.assert_awaited_once()
    task._logger.error.assert_called_once()
