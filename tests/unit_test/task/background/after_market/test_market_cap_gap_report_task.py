from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.schedulable_task import TaskState
from task.background.after_market.market_cap_gap_report_task import MarketCapGapReportTask


@pytest.fixture
def service():
    svc = MagicMock()
    svc.build_report = AsyncMock(return_value={
        "report_date": "20260625",
        "trigger": "kr_close",
        "fx_rate": 1400.0,
        "korean": [],
        "us": [],
        "comparisons": [],
    })
    return svc


@pytest.fixture
def reporter():
    rep = MagicMock()
    rep.send_market_cap_gap_report = AsyncMock()
    return rep


@pytest.mark.asyncio
async def test_on_market_closed_sends_report_once_per_date(service, reporter):
    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="kr_close",
        logger=MagicMock(),
    )

    await task._on_market_closed("20260625")
    await task._on_market_closed("20260625")

    service.build_report.assert_awaited_once_with(report_date="20260625", trigger="kr_close")
    reporter.send_market_cap_gap_report.assert_awaited_once()
    assert task.get_progress()["last_reported_date"] == "20260625"


@pytest.mark.asyncio
async def test_failed_report_does_not_mark_date(service, reporter):
    service.build_report.side_effect = RuntimeError("boom")
    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        logger=MagicMock(),
    )

    await task._on_market_closed("20260625")

    assert task.get_progress()["last_reported_date"] is None
    reporter.send_market_cap_gap_report.assert_not_called()


def test_us_close_task_uses_new_york_close_trigger(service, reporter):
    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        logger=MagicMock(),
    )

    assert task.task_name == "market_cap_gap_report_us"
    assert task._loop_timezone == "America/New_York"
    assert task._loop_cron_hour == 16
    assert task._loop_cron_minute == 30
    assert task.state == TaskState.IDLE


def test_kr_close_task_uses_korean_close_trigger(service, reporter):
    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="kr_close",
        logger=MagicMock(),
    )

    assert task.task_name == "market_cap_gap_report_kr"
    assert task._loop_timezone == "Asia/Seoul"
    assert task._loop_cron_hour == 15
    assert task._loop_cron_minute == 50
