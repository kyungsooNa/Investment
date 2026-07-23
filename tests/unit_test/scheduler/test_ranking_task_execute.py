# tests/unit_test/scheduler/test_ranking_task_execute.py
"""RankingTask.execute() — Ticket-driven 핸들러 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from task.background.after_market.ranking_task import RankingTask


def _make_task(**kwargs) -> RankingTask:
    broker = MagicMock()
    stock_repo = MagicMock()
    stock_repo.df = MagicMock()
    stock_repo.df.iterrows.return_value = iter([])
    task = RankingTask(
        broker_api_wrapper=broker,
        stock_code_repository=stock_repo,
        logger=MagicMock(),
        **kwargs,
    )
    return task


async def test_execute_skips_when_already_done():
    task = _make_task()
    task._basic_last_collected_date = "20250417"
    task._last_collected_date = "20250417"
    task._period_ranking_cache[("20250417", task.DEFAULT_PERIOD_RANKING_DAYS)] = []

    task.refresh_basic_ranking = AsyncMock()
    task.refresh_investor_ranking = AsyncMock()
    task.prewarm_period_ranking = AsyncMock()

    await task.execute({"date": "20250417"})

    task.refresh_basic_ranking.assert_not_called()
    task.refresh_investor_ranking.assert_not_called()
    task.prewarm_period_ranking.assert_not_called()


async def test_execute_runs_both_when_not_done():
    task = _make_task()
    task._basic_last_collected_date = None
    task._last_collected_date = None

    task.refresh_basic_ranking = AsyncMock()
    task.refresh_investor_ranking = AsyncMock()
    task.prewarm_period_ranking = AsyncMock()

    await task.execute({"date": "20250417"})

    task.refresh_basic_ranking.assert_called_once()
    task.refresh_investor_ranking.assert_called_once()
    task.prewarm_period_ranking.assert_awaited_once_with("20250417")
    assert task._basic_last_collected_date == "20250417"
    assert task._last_collected_date == "20250417"


async def test_execute_runs_only_investor_if_basic_done():
    task = _make_task()
    task._basic_last_collected_date = "20250417"
    task._last_collected_date = None

    task.refresh_basic_ranking = AsyncMock()
    task.refresh_investor_ranking = AsyncMock()
    task.prewarm_period_ranking = AsyncMock()

    await task.execute({"date": "20250417"})

    task.refresh_basic_ranking.assert_not_called()
    task.refresh_investor_ranking.assert_called_once()
    task.prewarm_period_ranking.assert_awaited_once_with("20250417")


async def test_execute_prewarms_default_period_when_daily_rankings_are_done():
    task = _make_task()
    task._basic_last_collected_date = "20250417"
    task._last_collected_date = "20250417"

    task.refresh_basic_ranking = AsyncMock()
    task.refresh_investor_ranking = AsyncMock()
    task.prewarm_period_ranking = AsyncMock()

    await task.execute({"date": "20250417"})

    task.refresh_basic_ranking.assert_not_called()
    task.refresh_investor_ranking.assert_not_called()
    task.prewarm_period_ranking.assert_awaited_once_with("20250417")


async def test_execute_sets_state_running_then_idle():
    from interfaces.schedulable_task import TaskState
    task = _make_task()

    states_during = []

    async def capture_state(payload=None):
        states_during.append(task.state)

    task.refresh_basic_ranking = capture_state
    task.refresh_investor_ranking = capture_state
    task.prewarm_period_ranking = capture_state

    await task.execute({"date": "20250417"})

    assert TaskState.RUNNING in states_during
    assert task.state == TaskState.IDLE


async def test_send_ranking_report_skips_same_trading_date_after_restart(tmp_path):
    state_path = tmp_path / "ranking_report_state.json"

    first_reporter = MagicMock()
    first_reporter.send_ranking_report = AsyncMock()
    first_task = _make_task(
        telegram_reporter=first_reporter,
        ranking_report_state_path=str(state_path),
    )

    await first_task._send_ranking_report_once({"foreign_buy": []}, "20260722")

    first_reporter.send_ranking_report.assert_awaited_once_with(
        {"foreign_buy": []},
        report_date="20260722",
    )

    restarted_reporter = MagicMock()
    restarted_reporter.send_ranking_report = AsyncMock()
    restarted_task = _make_task(
        telegram_reporter=restarted_reporter,
        ranking_report_state_path=str(state_path),
    )

    await restarted_task._send_ranking_report_once({"foreign_buy": []}, "20260722")

    restarted_reporter.send_ranking_report.assert_not_called()
