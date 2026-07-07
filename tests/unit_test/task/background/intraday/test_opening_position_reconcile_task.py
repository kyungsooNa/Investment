import asyncio
from datetime import datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from common.operator_alert_types import AlertSource
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


@pytest.mark.asyncio
async def test_should_run_now_false_without_clock_or_calendar():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_should_run_now_false_on_non_business_day():
    clock = MagicMock()
    open_time = datetime(2026, 4, 30, 9, 0)
    clock.get_market_open_time.return_value = open_time
    clock.get_current_kst_time.return_value = open_time + timedelta(seconds=61)
    mcs = AsyncMock()
    mcs.is_business_day.return_value = False
    task = OpeningPositionReconcileTask(
        reconcile_service=AsyncMock(), market_calendar_service=mcs, market_clock=clock
    )
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_should_run_now_false_when_business_day_check_raises():
    clock = MagicMock()
    open_time = datetime(2026, 4, 30, 9, 0)
    clock.get_market_open_time.return_value = open_time
    clock.get_current_kst_time.return_value = open_time + timedelta(seconds=61)
    mcs = AsyncMock()
    mcs.is_business_day.side_effect = RuntimeError("calendar down")
    task = OpeningPositionReconcileTask(
        reconcile_service=AsyncMock(), market_calendar_service=mcs, market_clock=clock
    )
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_suspend_resume_and_progress():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())

    task._state = TaskState.RUNNING
    await task.suspend()
    assert task.state == TaskState.SUSPENDED

    progress = task.get_progress()
    assert progress["running"] is False
    assert progress["last_result"] == {"mismatch_count": None, "error": None}

    await task.resume()
    assert task.state == TaskState.IDLE


@pytest.mark.asyncio
async def test_start_resets_state_after_stop():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())
    try:
        await task.start()
        await task.stop()
        assert task.state == TaskState.STOPPED

        # STOPPED 상태에서 재시작하면 IDLE로 복귀해야 한다.
        await task.start()
        assert task.state == TaskState.IDLE
    finally:
        await task.stop()


@pytest.mark.asyncio
async def test_loop_continues_when_suspended():
    task = OpeningPositionReconcileTask(reconcile_service=AsyncMock(), logger=MagicMock())
    task._state = TaskState.SUSPENDED
    task._should_run_now = AsyncMock()

    with patch(
        "task.background.intraday.opening_position_reconcile_task.asyncio.sleep",
        new=AsyncMock(side_effect=[None, asyncio.CancelledError()]),
    ):
        await task._loop()

    # SUSPENDED 동안에는 실행 여부 판단 자체를 건너뛴다.
    task._should_run_now.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_once_reports_to_operator_alert_on_mismatch():
    oas = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.return_value = {"mismatch_count": 2, "force_closed": ["A"]}
    task = OpeningPositionReconcileTask(
        reconcile_service=service, operator_alert_service=oas, logger=MagicMock()
    )

    await task.run_once()

    oas.report.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_once_resolves_operator_alert_when_ok():
    oas = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.return_value = {"mismatch_count": 0}
    task = OpeningPositionReconcileTask(
        reconcile_service=service, operator_alert_service=oas, logger=MagicMock()
    )

    await task.run_once()

    oas.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_once_reports_to_operator_alert_on_failure():
    oas = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.side_effect = RuntimeError("boom")
    task = OpeningPositionReconcileTask(
        reconcile_service=service, operator_alert_service=oas, logger=MagicMock()
    )

    result = await task.run_once()

    assert result["error"] == "boom"
    oas.report.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_once_reports_stale_broker_reconciled_positions():
    oas = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.return_value = {
        "mismatch_count": 0,
        "stale_broker_reconciled": [{"code": "006110", "days_held": 54}],
    }
    task = OpeningPositionReconcileTask(
        reconcile_service=service, operator_alert_service=oas, logger=MagicMock()
    )

    await task.run_once()

    oas.report.assert_any_call(
        AlertSource.RECONCILE,
        "reconcile:broker_reconciled_stale:006110",
        "warning",
        ANY,
        ANY,
        metadata={"code": "006110", "days_held": 54},
    )


@pytest.mark.asyncio
async def test_run_once_resolves_stale_broker_reconciled_alert_when_cleared():
    oas = AsyncMock()
    service = AsyncMock()
    service.reconcile_once.return_value = {
        "mismatch_count": 0,
        "stale_broker_reconciled": [{"code": "006110", "days_held": 54}],
    }
    task = OpeningPositionReconcileTask(
        reconcile_service=service, operator_alert_service=oas, logger=MagicMock()
    )
    await task.run_once()
    oas.reset_mock()

    service.reconcile_once.return_value = {"mismatch_count": 0, "stale_broker_reconciled": []}
    await task.run_once()

    oas.resolve.assert_any_call(
        AlertSource.RECONCILE, "reconcile:broker_reconciled_stale:006110", ANY
    )
