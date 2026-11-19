"""장전 코스피 방향 예측 태스크 테스트.

MarketTimingDailyUpdateTask 의 루프(개장 30분 전 창, 하루 1회, 거래일 판정)를 그대로 쓰고
실행 본문만 `PremarketDirectionService.refresh(notify=True)` 로 바꾼다.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.intraday.premarket_direction_forecast_task import (
    PremarketDirectionForecastTask,
)


def _make_task(*, business_day=True, forecast=None):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 11, 19, 8, 31)
    clock.get_market_open_time.return_value = datetime(2026, 11, 19, 9, 0)
    mcs = MagicMock()
    mcs.is_business_day = AsyncMock(return_value=business_day)
    service = MagicMock()
    service.refresh = AsyncMock(return_value=forecast or {"ok": True, "date": "2026-11-19"})
    task = PremarketDirectionForecastTask(
        premarket_direction_service=service,
        market_clock=clock,
        market_calendar_service=mcs,
        logger=MagicMock(),
    )
    return task, service


def test_task_name():
    task, _ = _make_task()
    assert task.task_name == "premarket_direction_forecast"


@pytest.mark.asyncio
async def test_should_run_in_pre_open_window():
    task, _ = _make_task()
    assert await task._should_run_now() is True


@pytest.mark.asyncio
async def test_run_once_refreshes_with_notification_and_marks_day_done():
    task, service = _make_task()

    result = await task.run_once()

    service.refresh.assert_awaited_once_with(notify=True)
    assert result == {"ok": True, "date": "20261119", "forecast": {"ok": True, "date": "2026-11-19"}}
    assert await task._should_run_now() is False


@pytest.mark.asyncio
async def test_run_once_failure_is_reported_and_retried_next_tick():
    task, service = _make_task(forecast={"ok": False, "date": "2026-11-19", "error": "boom"})

    result = await task.run_once()

    assert result["ok"] is False
    assert result["forecast"]["error"] == "boom"
    assert await task._should_run_now() is True


@pytest.mark.asyncio
async def test_run_once_skips_non_business_day():
    task, service = _make_task(business_day=False)

    result = await task.run_once()

    service.refresh.assert_not_awaited()
    assert result == {"ok": True, "skipped": "non_business_day", "date": "20261119"}
