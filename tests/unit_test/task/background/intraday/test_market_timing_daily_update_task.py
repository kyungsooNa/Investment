from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.schedulable_task import TaskState
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
