from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from interfaces.schedulable_task import TaskState
from task.background.after_market.newhigh_strategy_coverage_backtest_task import (
    NewHighStrategyCoverageBacktestTask,
)


def _make_task(service=None, mcs=None):
    service = service or MagicMock()
    service.run = AsyncMock()
    return NewHighStrategyCoverageBacktestTask(
        coverage_service=service,
        mcs=mcs,
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


async def test_on_market_closed_runs_coverage_service():
    service = MagicMock()
    service.run = AsyncMock()
    task = _make_task(service=service)

    await task._on_market_closed("20260505")

    service.run.assert_awaited_once_with("20260505")


async def test_force_run_uses_market_calendar_date():
    service = MagicMock()
    service.run = AsyncMock()
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260502")
    task = _make_task(service=service, mcs=mcs)

    await task.force_run()

    service.run.assert_awaited_once_with("20260502")
    assert task.state == TaskState.IDLE


async def test_force_run_falls_back_to_today_when_calendar_fails():
    service = MagicMock()
    service.run = AsyncMock()
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(side_effect=RuntimeError("calendar down"))
    task = _make_task(service=service, mcs=mcs)

    with patch("task.background.after_market.newhigh_strategy_coverage_backtest_task.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "20260505"
        await task.force_run()

    service.run.assert_awaited_once_with("20260505")
    task._logger.warning.assert_called_once()


def test_task_name_and_scheduler_label():
    task = _make_task()
    assert task.task_name == "newhigh_strategy_coverage_backtest"
    assert task._scheduler_label == "NewHighStrategyCoverageBacktestTask"


async def test_on_market_closed_swallows_exception():
    task = _make_task()
    task._coverage_service.run = AsyncMock(side_effect=RuntimeError("boom"))

    await task._on_market_closed("20260505")

    task._logger.error.assert_called_once()
    assert task._last_result is None


def test_get_progress_with_result():
    task = _make_task()
    task._last_result = SimpleNamespace(
        target_date="20260505",
        skipped=False,
        skip_reason="",
        newhigh_count=10,
        strategy_count=2,
        all_strategy_missed_count=3,
        all_strategy_missed_rate=0.3,
    )

    progress = task.get_progress()

    assert progress["last_result"] == {
        "target_date": "20260505",
        "skipped": False,
        "skip_reason": "",
        "newhigh_count": 10,
        "strategy_count": 2,
        "all_strategy_missed_count": 3,
        "all_strategy_missed_rate": 0.3,
    }
