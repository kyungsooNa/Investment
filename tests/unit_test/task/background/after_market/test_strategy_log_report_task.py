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
    svc.get_last_execution_quality_candidates.return_value = []
    svc.get_last_strategy_degradation_candidates.return_value = []
    return svc


@pytest.fixture
def mock_notification_service():
    svc = MagicMock()
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def mock_operator_alert_service():
    svc = MagicMock()
    svc.report = AsyncMock()
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

    async def test_execution_quality_candidates_emit_strategy_warning(
        self, task, mock_report_service, mock_notification_service
    ):
        mock_report_service.get_last_execution_quality_candidates.return_value = [{
            "strategy": "잔량전략",
            "period": "4-2 적용 후",
            "reason": "평균 잔량 73.3%",
            "count": 3,
        }]

        await task._on_market_closed("20260419")

        mock_notification_service.emit.assert_awaited_once()
        args, kwargs = mock_notification_service.emit.await_args
        assert args[0].value == "STRATEGY"
        assert args[1].value == "warning"
        assert args[2] == "체결 품질 비활성화 후보"
        assert "잔량전략" in args[3]
        assert kwargs["metadata"]["alert_type"] == "execution_quality_candidate"

    async def test_strategy_degradation_candidates_use_operator_alert_only(
        self, mock_report_service, mock_notification_service, mock_operator_alert_service
    ):
        mock_report_service.get_last_strategy_degradation_candidates.return_value = [{
            "strategy": "S1",
            "status": "critical_candidate",
            "reasons": ["consecutive_losses"],
            "recommended_actions": ["pause_new_entries_candidate"],
            "backtest_live_divergence": {"matched_count": 1, "avg_net_return_diff": -4.0},
        }]
        kill_switch = MagicMock()
        kill_switch.is_strategy_tripped.return_value = {
            "trip_reason": "연속 손실 5회",
            "trip_timestamp": "2026-05-15T15:10:00+09:00",
        }
        task = StrategyLogReportTask(
            report_service=mock_report_service,
            notification_service=mock_notification_service,
            operator_alert_service=mock_operator_alert_service,
            kill_switch_service=kill_switch,
            logger=MagicMock(),
        )

        await task._on_market_closed("20260419")

        mock_operator_alert_service.report.assert_awaited_once()
        mock_notification_service.emit.assert_not_awaited()
        args, kwargs = mock_operator_alert_service.report.await_args
        assert args[0].value == "STRATEGY_PERF"
        assert args[1] == "strategy_perf:S1"
        assert args[2] == "critical"
        assert kwargs["metadata"]["already_blocked_by_kill_switch"] is True
        assert kwargs["metadata"]["kill_switch_trip"]["trip_reason"] == "연속 손실 5회"
        assert kwargs["metadata"]["candidate"]["backtest_live_divergence"]["matched_count"] == 1

    async def test_strategy_degradation_candidates_fallback_to_notification(
        self, mock_report_service, mock_notification_service
    ):
        mock_report_service.get_last_strategy_degradation_candidates.return_value = [{
            "strategy": "S1",
            "status": "degraded",
            "reasons": ["profit_factor_low"],
            "recommended_actions": ["reduce_position_size_candidate"],
        }]
        task = StrategyLogReportTask(
            report_service=mock_report_service,
            notification_service=mock_notification_service,
            logger=MagicMock(),
        )

        await task._on_market_closed("20260419")

        mock_notification_service.emit.assert_awaited_once()
        args, kwargs = mock_notification_service.emit.await_args
        assert args[0].value == "STRATEGY"
        assert args[1].value == "warning"
        assert args[2] == "전략 성과 저하 후보"
        assert kwargs["metadata"]["alert_type"] == "strategy_degradation_candidate"

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

    async def test_notification_service_without_candidate_getter_returns(
        self, mock_notification_service
    ):
        class ReportServiceWithoutGetter:
            def __init__(self):
                self.generate_report = AsyncMock(return_value="<html>report</html>")

        report_service = ReportServiceWithoutGetter()
        task = StrategyLogReportTask(
            report_service=report_service,
            notification_service=mock_notification_service,
            logger=MagicMock(),
        )

        await task._on_market_closed("20260419")

        mock_notification_service.emit.assert_not_awaited()

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

    async def test_force_run_uses_market_calendar_service_date(
        self, mock_report_service, mock_notification_service
    ):
        mcs = MagicMock()
        mcs.get_latest_trading_date = AsyncMock(return_value="20260418")
        task = StrategyLogReportTask(
            report_service=mock_report_service,
            notification_service=mock_notification_service,
            mcs=mcs,
            logger=MagicMock(),
        )

        await task.force_run()

        mock_report_service.generate_report.assert_awaited_once_with("20260418")

    async def test_force_run_falls_back_to_today_when_calendar_fails(
        self, mock_report_service, mock_notification_service
    ):
        mcs = MagicMock()
        mcs.get_latest_trading_date = AsyncMock(side_effect=RuntimeError("calendar down"))
        task = StrategyLogReportTask(
            report_service=mock_report_service,
            notification_service=mock_notification_service,
            mcs=mcs,
            logger=MagicMock(),
        )

        with patch(
            "task.background.after_market.strategy_log_report_task.datetime"
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260419"
            await task.force_run()

        mock_report_service.generate_report.assert_awaited_once_with("20260419")
        task._logger.warning.assert_called_once()
