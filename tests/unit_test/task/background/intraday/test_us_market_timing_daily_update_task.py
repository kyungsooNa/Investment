"""미국장 마켓타이밍 일일 갱신 태스크 테스트.

국내 MarketTimingDailyUpdateTask 를 상속해 루프/상태머신은 그대로 쓰고,
거래일 판정(USMarketCalendarService.is_trading_day — 동기)과 태스크명만 갈아끼운다.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from interfaces.schedulable_task import TaskPriority
from task.background.intraday.us_market_timing_daily_update_task import (
    USMarketTimingDailyUpdateTask,
)

NY = pytz.timezone("America/New_York")


def _make_task(*, calendar=None, regime=None, now=None):
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = now or NY.localize(
        datetime(2026, 8, 18, 9, 10)
    )
    market_clock.get_market_open_time.return_value = NY.localize(
        datetime(2026, 8, 18, 9, 30)
    )
    if regime is None:
        regime = MagicMock()
        regime.refresh_market_timing = AsyncMock(return_value={"US": True})
    return USMarketTimingDailyUpdateTask(
        us_market_regime_service=regime,
        market_clock=market_clock,
        us_market_calendar_service=calendar,
        logger=MagicMock(),
    ), regime


def test_task_name_is_distinct_from_domestic():
    """국내 태스크와 이름이 겹치면 스케줄러 등록이 충돌한다."""
    task, _ = _make_task()
    assert task.task_name == "us_market_timing_daily_update"
    assert task.priority == TaskPriority.NORMAL


@pytest.mark.asyncio
async def test_run_once_refreshes_us_regime():
    calendar = MagicMock()
    calendar.is_trading_day.return_value = True
    task, regime = _make_task(calendar=calendar)

    result = await task.run_once()

    regime.refresh_market_timing.assert_awaited_once()
    assert regime.refresh_market_timing.await_args.kwargs["caller"] == task.task_name
    assert result == {"ok": True, "date": "20260818", "markets": {"US": True}}
    calendar.is_trading_day.assert_called_with("20260818")


@pytest.mark.asyncio
async def test_run_once_skips_us_holiday():
    calendar = MagicMock()
    calendar.is_trading_day.return_value = False  # 예: 독립기념일
    task, regime = _make_task(calendar=calendar)

    result = await task.run_once()

    regime.refresh_market_timing.assert_not_awaited()
    assert result == {"ok": True, "skipped": "non_business_day", "date": "20260818"}


@pytest.mark.asyncio
async def test_should_run_once_per_date_in_ny_pre_open_window():
    calendar = MagicMock()
    calendar.is_trading_day.return_value = True
    task, _ = _make_task(calendar=calendar)

    assert await task._should_run_now() is True
    task._last_checked_date = "20260818"
    assert await task._should_run_now() is False

    task._last_checked_date = None
    # 개장 30분 전 창 밖(09:30 개장 → 08:59 는 이름) 및 개장 후는 실행하지 않는다
    task._market_clock.get_current_kst_time.return_value = NY.localize(
        datetime(2026, 8, 18, 8, 55)
    )
    assert await task._should_run_now() is False
    task._market_clock.get_current_kst_time.return_value = NY.localize(
        datetime(2026, 8, 18, 9, 31)
    )
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_should_run_now_false_on_us_holiday():
    calendar = MagicMock()
    calendar.is_trading_day.return_value = False
    task, _ = _make_task(calendar=calendar)

    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_calendar_failure_is_reported_and_blocks_refresh():
    calendar = MagicMock()
    calendar.is_trading_day.side_effect = RuntimeError("calendar down")
    task, regime = _make_task(calendar=calendar)

    result = await task.run_once()

    assert result == {"ok": False, "error": "calendar down", "date": "20260818"}
    regime.refresh_market_timing.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_calendar_refreshes_directly():
    task, regime = _make_task(calendar=None)

    result = await task.run_once()

    regime.refresh_market_timing.assert_awaited_once()
    assert result["markets"] == {"US": True}
