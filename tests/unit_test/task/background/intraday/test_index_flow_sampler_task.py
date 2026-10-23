from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.schedulable_task import TaskState
from task.background.intraday.index_flow_sampler_task import IndexFlowSamplerTask


def _build_task(*, market_open=True, business_day=True):
    history = MagicMock()
    history.record_sample = AsyncMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 10, 23, 10, 0)
    market_clock.is_market_operating_hours.return_value = market_open
    calendar = MagicMock()
    calendar.is_business_day = AsyncMock(return_value=business_day)
    task = IndexFlowSamplerTask(
        index_flow_history_service=history,
        market_clock=market_clock,
        market_calendar_service=calendar,
        logger=MagicMock(),
    )
    return task, history


@pytest.mark.asyncio
async def test_tick_records_sample_during_market_hours():
    task, history = _build_task()

    await task._tick()

    history.record_sample.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("market_open, business_day", [(False, True), (True, False)])
async def test_tick_skips_outside_market_or_on_holiday(market_open, business_day):
    task, history = _build_task(market_open=market_open, business_day=business_day)

    await task._tick()

    history.record_sample.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_marks_task_stopped():
    task, _ = _build_task()

    await task.start()
    await task.stop()

    assert task.task_name == "index_flow_sampler"
    assert task.state == TaskState.STOPPED
