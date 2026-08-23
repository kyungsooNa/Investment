import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.market_timing_daily_update_task import MarketTimingDailyUpdateTask


@pytest.mark.asyncio
async def test_run_once_refreshes_market_timing_with_stable_caller():
    universe = MagicMock()
    universe.refresh_market_timing = AsyncMock(return_value={"KOSDAQ": True, "KOSPI": False})
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 18, 8, 40)
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=True)
    task = MarketTimingDailyUpdateTask(
        universe_service=universe,
        market_clock=market_clock,
        market_calendar_service=calendar,
        logger=MagicMock(),
    )

    result = await task.run_once()

    universe.refresh_market_timing.assert_awaited_once()
    assert universe.refresh_market_timing.await_args.kwargs["caller"] == task.task_name
    assert result == {"ok": True, "date": "20260818", "markets": {"KOSDAQ": True, "KOSPI": False}}
    assert task.get_progress()["last_checked_date"] == "20260818"


@pytest.mark.asyncio
async def test_run_once_skips_non_business_day_without_touching_universe():
    universe = MagicMock()
    universe.refresh_market_timing = AsyncMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 15, 8, 40)
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=False)
    task = MarketTimingDailyUpdateTask(
        universe_service=universe,
        market_clock=market_clock,
        market_calendar_service=calendar,
        logger=MagicMock(),
    )

    result = await task.run_once()

    universe.refresh_market_timing.assert_not_awaited()
    assert result == {"ok": True, "skipped": "non_business_day", "date": "20260815"}


@pytest.mark.asyncio
async def test_should_run_once_per_date_in_pre_open_window():
    universe = MagicMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 18, 8, 40)
    market_clock.get_market_open_time.return_value = datetime(2026, 8, 18, 9, 0)
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=True)
    task = MarketTimingDailyUpdateTask(
        universe_service=universe,
        market_clock=market_clock,
        market_calendar_service=calendar,
        logger=MagicMock(),
    )

    assert await task._should_run_now() is True
    task._last_checked_date = "20260818"
    assert await task._should_run_now() is False

    task._last_checked_date = None
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 18, 8, 29)
    assert await task._should_run_now() is False
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 18, 9, 1)
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_start_stop_cancels_background_loop():
    task = MarketTimingDailyUpdateTask(
        universe_service=MagicMock(),
        market_clock=MagicMock(),
        market_calendar_service=MagicMock(),
        check_interval_sec=3600,
        logger=MagicMock(),
    )

    await task.start()
    assert task.state == TaskState.IDLE
    await task.stop()
    assert task.state == TaskState.STOPPED


def _make_task(*, calendar=None, market_clock=None, universe=None):
    if market_clock is None:
        market_clock = MagicMock()
        market_clock.get_current_kst_time.return_value = datetime(2026, 8, 18, 8, 40)
        market_clock.get_market_open_time.return_value = datetime(2026, 8, 18, 9, 0)
    if universe is None:
        universe = MagicMock()
        universe.refresh_market_timing = AsyncMock(return_value={})
    return MarketTimingDailyUpdateTask(
        universe_service=universe,
        market_clock=market_clock,
        market_calendar_service=calendar,
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_run_once_reports_error_when_business_day_check_raises():
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(side_effect=RuntimeError("calendar down"))
    universe = MagicMock()
    universe.refresh_market_timing = AsyncMock()
    task = _make_task(calendar=calendar, universe=universe)

    result = await task.run_once()

    assert result == {"ok": False, "error": "calendar down", "date": "20260818"}
    universe.refresh_market_timing.assert_not_awaited()
    task._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_run_once_without_calendar_refreshes_directly():
    universe = MagicMock()
    universe.refresh_market_timing = AsyncMock(return_value={"KOSPI": True})
    task = _make_task(calendar=None, universe=universe)

    result = await task.run_once()

    universe.refresh_market_timing.assert_awaited_once()
    assert result["markets"] == {"KOSPI": True}


@pytest.mark.asyncio
async def test_should_run_now_returns_false_without_market_clock():
    task = MarketTimingDailyUpdateTask(
        universe_service=MagicMock(),
        market_clock=None,
        logger=MagicMock(),
    )

    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_should_run_now_returns_false_on_non_business_day():
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=False)
    task = _make_task(calendar=calendar)

    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_should_run_now_returns_false_when_business_day_check_raises():
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(side_effect=RuntimeError("calendar down"))
    task = _make_task(calendar=calendar)

    assert await task._should_run_now() is False
    task._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_loop_runs_once_when_due_then_logs_error_and_exits_on_cancel():
    task = _make_task()
    task._should_run_now = AsyncMock(
        side_effect=[True, RuntimeError("boom"), asyncio.CancelledError()]
    )
    task.run_once = AsyncMock()

    with patch(
        "task.background.intraday.market_timing_daily_update_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    task.run_once.assert_awaited_once()
    assert task.state == TaskState.IDLE
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_run_while_suspended():
    task = _make_task()
    task._should_run_now = AsyncMock()
    task.run_once = AsyncMock()
    task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch(
        "task.background.intraday.market_timing_daily_update_task.asyncio.sleep",
        sleep_mock,
    ):
        await task._loop()

    task._should_run_now.assert_not_awaited()
    task.run_once.assert_not_awaited()
    assert task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_loop_does_not_run_when_not_due():
    task = _make_task()
    task._should_run_now = AsyncMock(side_effect=[False, asyncio.CancelledError()])
    task.run_once = AsyncMock()

    with patch(
        "task.background.intraday.market_timing_daily_update_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    task.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_after_stop_resets_state_to_idle():
    task = _make_task()

    await task.stop()
    assert task.state == TaskState.STOPPED

    await task.start()
    assert task.state == TaskState.IDLE
    first_task = task._task
    await task.start()
    assert task._task is first_task

    await task.stop()
    assert task._task is None


@pytest.mark.asyncio
async def test_suspend_and_resume_toggle_state():
    task = _make_task()

    await task.suspend()
    assert task.state == TaskState.SUSPENDED

    await task.resume()
    assert task.state == TaskState.IDLE

    await task.resume()
    assert task.state == TaskState.IDLE


def test_task_identity_and_progress_defaults():
    task = _make_task()

    assert task.task_name == "market_timing_daily_update"
    assert task.priority == TaskPriority.NORMAL
    assert task.get_progress() == {
        "running": False,
        "last_checked_date": None,
        "last_result": {"ok": True},
    }
