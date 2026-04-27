"""StrategyLogReportTask 단위 테스트."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from task.background.after_market.strategy_log_report_task import StrategyLogReportTask
from interfaces.schedulable_task import TaskPriority, TaskState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_report_service():
    svc = MagicMock()
    svc.generate_report = AsyncMock(return_value="<html>report</html>")
    return svc


@pytest.fixture
def mock_notification_service():
    svc = MagicMock()
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def mock_telegram():
    tg = MagicMock()
    tg.send_strategy_log_report = AsyncMock()
    return tg


@pytest.fixture
def task(mock_report_service, mock_notification_service, mock_telegram):
    return StrategyLogReportTask(
        report_service=mock_report_service,
        notification_service=mock_notification_service,
        telegram_reporter=mock_telegram,
        logger=MagicMock(),
    )


@pytest.fixture
def task_minimal(mock_report_service):
    """notification_service, telegram_reporter 없이 생성한 최소 태스크."""
    return StrategyLogReportTask(
        report_service=mock_report_service,
        logger=MagicMock(),
    )


# ---------------------------------------------------------------------------
# 태스크 속성
# ---------------------------------------------------------------------------

class TestTaskProperties:

    def test_task_name(self, task):
        assert task.task_name == "strategy_log_report"

    def test_scheduler_label(self, task):
        assert task._scheduler_label == "StrategyLogReportTask"

    def test_initial_state(self, task):
        assert task.state == TaskState.IDLE

    def test_priority(self, task):
        assert task.priority == TaskPriority.LOW

    def test_get_progress_idle(self, task):
        assert task.get_progress() == {"running": False}


# ---------------------------------------------------------------------------
# _on_market_closed
# ---------------------------------------------------------------------------

class TestOnMarketClosed:

    async def test_generate_report_called_with_date(self, task, mock_report_service):
        await task._on_market_closed("20260419")
        mock_report_service.generate_report.assert_awaited_once_with("20260419")

    async def test_telegram_reporter_receives_report_html(self, task, mock_telegram):
        await task._on_market_closed("20260419")
        mock_telegram.send_strategy_log_report.assert_awaited_once_with(
            "<html>report</html>", "20260419"
        )

    async def test_telegram_send_called(self, task, mock_telegram):
        await task._on_market_closed("20260419")
        mock_telegram.send_strategy_log_report.assert_awaited_once_with(
            "<html>report</html>", "20260419"
        )

    async def test_report_generation_failure_skips_notification_and_telegram(
        self, task, mock_report_service, mock_notification_service, mock_telegram
    ):
        mock_report_service.generate_report.side_effect = RuntimeError("fail")
        await task._on_market_closed("20260419")
        mock_notification_service.emit.assert_not_awaited()
        mock_telegram.send_strategy_log_report.assert_not_awaited()

    async def test_no_notification_service_does_not_raise(
        self, task_minimal, mock_telegram
    ):
        task_minimal._telegram_reporter = mock_telegram
        await task_minimal._on_market_closed("20260419")
        mock_telegram.send_strategy_log_report.assert_awaited_once()

    async def test_no_telegram_reporter_does_not_raise(
        self, mock_report_service, mock_notification_service
    ):
        task = StrategyLogReportTask(
            report_service=mock_report_service,
            notification_service=mock_notification_service,
            logger=MagicMock(),
        )
        await task._on_market_closed("20260419")
        mock_report_service.generate_report.assert_awaited_once_with("20260419")

    async def test_telegram_failure_logs_warning_and_does_not_raise(
        self, task, mock_telegram
    ):
        mock_telegram.send_strategy_log_report.side_effect = Exception("network")
        await task._on_market_closed("20260419")  # should not propagate

    async def test_report_generation_failure_logs_error(
        self, task, mock_report_service
    ):
        mock_report_service.generate_report.side_effect = ValueError("bad")
        await task._on_market_closed("20260419")
        task._logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# get_progress
# ---------------------------------------------------------------------------

class TestGetProgress:

    def test_returns_false_when_idle(self, task):
        assert task.get_progress() == {"running": False}

    def test_returns_true_when_running(self, task):
        task._state = TaskState.RUNNING
        assert task.get_progress() == {"running": True}

    def test_returns_false_when_stopped(self, task):
        task._state = TaskState.STOPPED
        assert task.get_progress() == {"running": False}


# ---------------------------------------------------------------------------
# force_run
# ---------------------------------------------------------------------------

class TestForceRun:

    async def test_force_run_calls_on_market_closed_with_today(self, task):
        with patch(
            "task.background.after_market.strategy_log_report_task.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260419"
            await task.force_run()
        task._report_service.generate_report.assert_awaited_once_with("20260419")

    async def test_force_run_state_returns_to_idle_after(self, task):
        with patch(
            "task.background.after_market.strategy_log_report_task.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260419"
            await task.force_run()
        assert task.state == TaskState.IDLE
