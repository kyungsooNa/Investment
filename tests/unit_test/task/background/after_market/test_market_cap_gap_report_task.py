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


class _FakeStore:
    """save_keyed/load_keyed 만 흉내내는 인메모리 스토어 (재시작 영속화 검증용)."""

    def __init__(self):
        self._data = {}

    def save_keyed(self, key, value):
        self._data[key] = value

    def load_keyed(self, key):
        return self._data.get(key)


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
async def test_persisted_date_skips_resend_after_restart(service, reporter):
    """전송 후 재시작(태스크 재생성)해도 같은 날짜는 재전송하지 않는다."""
    store = _FakeStore()

    task1 = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        scheduler_store=store,
        logger=MagicMock(),
    )
    await task1._on_market_closed("20260625")
    reporter.send_market_cap_gap_report.assert_awaited_once()

    # 프로그램 재시작 시뮬레이션: 같은 store 로 새 태스크 생성 (인메모리 상태 소실)
    task2 = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        scheduler_store=store,
        logger=MagicMock(),
    )
    await task2._on_market_closed("20260625")

    # build_report/전송은 최초 1회뿐이어야 한다 (catch-up 재전송 방지)
    service.build_report.assert_awaited_once()
    reporter.send_market_cap_gap_report.assert_awaited_once()


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
