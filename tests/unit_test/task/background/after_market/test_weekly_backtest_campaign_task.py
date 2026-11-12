from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.after_market.weekly_backtest_campaign_task import (
    WeeklyBacktestCampaignTask,
)


def _make_task(*, valid_date_count=30, last_completed=None, next_open="20261116"):
    service = MagicMock()
    service.build_plan.return_value = {
        "valid_date_count": valid_date_count,
        "valid_dates": ["20261001"] * valid_date_count,
    }
    service.run.return_value = {
        "status": "completed",
        "summary": {"completed": 7, "failed": 0, "zero_trade": 1},
    }
    store = MagicMock()
    store.load_keyed.return_value = last_completed
    mcs = MagicMock()
    mcs.get_next_open_day = AsyncMock(return_value=next_open)
    task = WeeklyBacktestCampaignTask(
        campaign_service=service,
        scheduler_store=store,
        min_valid_dates=30,
        mcs=mcs,
        market_clock=MagicMock(),
        logger=MagicMock(),
    )
    return task, service, store


@pytest.mark.asyncio
async def test_skips_when_not_last_trading_day_of_week():
    task, service, store = _make_task(next_open="20261113")

    await task._on_market_closed("20261112")

    service.build_plan.assert_not_called()
    service.run.assert_not_called()
    store.save_keyed.assert_not_called()
    assert task.get_progress()["status"] == "skipped_not_week_end"


@pytest.mark.asyncio
async def test_waits_until_minimum_valid_dates_are_available():
    task, service, store = _make_task(valid_date_count=29)

    await task._on_market_closed("20261113")

    service.run.assert_not_called()
    store.save_keyed.assert_not_called()
    progress = task.get_progress()
    assert progress["status"] == "waiting_for_data"
    assert progress["valid_date_count"] == 29
    assert progress["required_valid_dates"] == 30


@pytest.mark.asyncio
async def test_runs_once_on_week_end_and_persists_completion():
    task, service, store = _make_task()

    await task._on_market_closed("20261113")

    service.run.assert_called_once_with()
    store.save_keyed.assert_called_once_with(
        "weekly_backtest_campaign_last_completed", "20261113"
    )
    progress = task.get_progress()
    assert progress["status"] == "completed"
    assert progress["last_completed_date"] == "20261113"
    assert progress["summary"]["completed"] == 7


@pytest.mark.asyncio
async def test_skips_when_current_week_already_completed():
    task, service, store = _make_task(last_completed="20261112")

    await task._on_market_closed("20261113")

    service.build_plan.assert_not_called()
    service.run.assert_not_called()
    store.save_keyed.assert_not_called()
    assert task.get_progress()["status"] == "skipped_already_completed"


@pytest.mark.asyncio
async def test_failure_is_reported_without_marking_week_complete():
    task, service, store = _make_task()
    service.run.side_effect = RuntimeError("campaign failed")

    await task._on_market_closed("20261113")

    store.save_keyed.assert_not_called()
    progress = task.get_progress()
    assert progress["status"] == "failed"
    assert "campaign failed" in progress["error"]
