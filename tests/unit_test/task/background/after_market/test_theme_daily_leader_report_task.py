"""ThemeDailyLeaderReportTask 단위 테스트."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from task.background.after_market.theme_daily_leader_report_task import ThemeDailyLeaderReportTask


def _make_task(*, ranking_source=None, service_response=None):
    ranking_task = MagicMock()
    ranking_task.get_daily_theme_report_rankings.return_value = ranking_source or {
        "report_date": "20260630",
        "all_stocks": [{"stck_shrn_iscd": "005930"}],
    }
    theme_service = MagicMock()
    theme_service.build_daily_theme_report = AsyncMock(
        return_value=service_response or ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data=[{"normalized_name": "반도체/소부장", "leaders": []}],
        )
    )
    telegram_reporter = AsyncMock()
    notification_service = AsyncMock()
    logger = MagicMock()
    task = ThemeDailyLeaderReportTask(
        ranking_task=ranking_task,
        theme_daily_leader_service=theme_service,
        telegram_reporter=telegram_reporter,
        notification_service=notification_service,
        logger=logger,
    )
    return SimpleNamespace(
        task=task,
        ranking_task=ranking_task,
        theme_service=theme_service,
        telegram_reporter=telegram_reporter,
        notification_service=notification_service,
        logger=logger,
    )


@pytest.mark.asyncio
async def test_execute_sends_daily_theme_report_from_ranking_cache():
    deps = _make_task()

    await deps.task.execute({"date": "20260630"})

    deps.theme_service.build_daily_theme_report.assert_awaited_once_with(
        {
            "report_date": "20260630",
            "all_stocks": [{"stck_shrn_iscd": "005930"}],
        },
        report_date="20260630",
    )
    deps.telegram_reporter.send_daily_theme_report.assert_awaited_once_with(
        [{"normalized_name": "반도체/소부장", "leaders": []}],
        report_date="20260630",
    )
    progress = deps.task.get_progress()
    assert progress["running"] is False
    assert progress["last_report_date"] == "20260630"
    assert progress["sent_count"] == 1
    assert progress["last_error"] is None


@pytest.mark.asyncio
async def test_execute_skips_when_ranking_cache_is_empty():
    deps = _make_task(ranking_source={"report_date": "20260630", "all_stocks": []})

    await deps.task.execute({"date": "20260630"})

    deps.theme_service.build_daily_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    deps.notification_service.emit.assert_awaited_once()
    assert deps.task.get_progress()["last_error"] == "ranking_cache_empty"


@pytest.mark.asyncio
async def test_force_run_uses_latest_trading_date_from_calendar():
    deps = _make_task()
    deps.task._mcs = AsyncMock()
    deps.task._mcs.get_latest_trading_date.return_value = "20260701"

    await deps.task.force_run()

    deps.theme_service.build_daily_theme_report.assert_awaited_once()
    assert deps.theme_service.build_daily_theme_report.call_args.kwargs["report_date"] == "20260701"


def test_task_identity_and_initial_progress():
    deps = _make_task()

    assert deps.task.task_name == "daily_theme_leader_report"
    assert deps.task.get_progress() == {
        "running": False,
        "last_report_date": None,
        "sent_count": 0,
        "last_error": None,
    }
