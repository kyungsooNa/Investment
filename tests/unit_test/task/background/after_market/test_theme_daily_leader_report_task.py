"""ThemeDailyLeaderReportTask 단위 테스트."""
from datetime import datetime
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


@pytest.mark.asyncio
async def test_execute_skips_without_notification_service_when_cache_empty():
    deps = _make_task(ranking_source={"all_stocks": []})
    deps.task._notification_service = None

    await deps.task.execute({"date": "20260630"})

    deps.theme_service.build_daily_theme_report.assert_not_awaited()
    assert deps.task.get_progress()["last_error"] == "ranking_cache_empty"
    assert deps.task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_execute_marks_error_when_theme_service_response_fails():
    deps = _make_task(service_response=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="테마 생성 실패", data=None
    ))

    await deps.task.execute({"date": "20260630"})

    assert deps.task.get_progress()["last_error"] == "theme_service_error:테마 생성 실패"
    deps.telegram_reporter.send_daily_theme_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_marks_error_when_theme_service_returns_nothing():
    deps = _make_task()
    deps.theme_service.build_daily_theme_report = AsyncMock(return_value=None)

    await deps.task.execute({"date": "20260630"})

    assert deps.task.get_progress()["last_error"] == "theme_service_error:응답 없음"


@pytest.mark.asyncio
async def test_execute_records_report_date_without_telegram_reporter():
    deps = _make_task()
    deps.task._telegram_reporter = None

    await deps.task.execute({"date": "20260630"})

    assert deps.task.get_progress()["last_report_date"] == "20260630"
    assert deps.task.get_progress()["sent_count"] == 1
    assert deps.task.get_progress()["last_error"] is None


@pytest.mark.asyncio
async def test_execute_captures_unexpected_error_and_clears_running_flag():
    deps = _make_task()
    deps.ranking_task.get_daily_theme_report_rankings.side_effect = RuntimeError("cache boom")

    await deps.task.execute({"date": "20260630"})

    assert deps.task.get_progress()["last_error"] == "cache boom"
    assert deps.task.get_progress()["running"] is False
    deps.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_force_run_falls_back_to_today_when_calendar_lookup_fails():
    deps = _make_task()
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(side_effect=RuntimeError("calendar down"))
    deps.task._mcs = mcs

    await deps.task.force_run()

    deps.logger.warning.assert_called_once()
    assert deps.task.get_progress()["last_report_date"] == datetime.now().strftime("%Y%m%d")


@pytest.mark.asyncio
async def test_force_run_uses_today_without_calendar_service():
    deps = _make_task()
    deps.task._mcs = None

    await deps.task.force_run()

    assert deps.task.get_progress()["last_report_date"] == datetime.now().strftime("%Y%m%d")


def test_scheduler_label_is_stable():
    deps = _make_task()

    assert deps.task._scheduler_label == "ThemeDailyLeaderReportTask"
