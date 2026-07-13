from unittest.mock import AsyncMock, MagicMock

import pytest

from task.background.after_market.ytd_ranking_report_task import YtdRankingReportTask


class _FakeStore:
    def __init__(self):
        self.data = {}

    def load_keyed(self, key):
        return self.data.get(key)

    def save_keyed(self, key, value):
        self.data[key] = value


def _make_task(*, next_open="20260720", rows=None, store=None):
    repository = MagicMock()
    repository.get_ytd_return_ranking = AsyncMock(return_value=[{
        "code": "005930",
        "name": "삼성전자",
        "current_price": 75000,
        "base_price": 50000,
        "base_date": "20260102",
        "latest_date": "20260717",
        "ytd_return_rate": 50.0,
    }] if rows is None else rows)
    reporter = MagicMock()
    reporter.send_ytd_ranking_report = AsyncMock(return_value=True)
    mcs = MagicMock()
    mcs.get_next_open_day = AsyncMock(return_value=next_open)
    task = YtdRankingReportTask(
        stock_repository=repository,
        telegram_reporter=reporter,
        market_calendar_service=mcs,
        scheduler_store=store,
        logger=MagicMock(),
    )
    return task, repository, reporter


@pytest.mark.asyncio
async def test_friday_close_sends_weekly_report_once():
    task, repository, reporter = _make_task(next_open="20260720")

    await task._on_market_closed("20260717")
    await task._on_market_closed("20260717")

    repository.get_ytd_return_ranking.assert_awaited_once_with(limit=20)
    reporter.send_ytd_ranking_report.assert_awaited_once()
    assert task.get_progress()["last_reported_date"] == "20260717"


@pytest.mark.asyncio
async def test_thursday_sends_when_friday_is_closed():
    task, _, reporter = _make_task(next_open="20260720")

    await task._on_market_closed("20260716")

    reporter.send_ytd_ranking_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_thursday_skips_when_friday_is_open():
    task, repository, reporter = _make_task(next_open="20260717")

    await task._on_market_closed("20260716")

    repository.get_ytd_return_ranking.assert_not_awaited()
    reporter.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_week_prevents_resend_after_restart():
    store = _FakeStore()
    task1, _, reporter1 = _make_task(store=store)
    await task1._on_market_closed("20260717")
    reporter1.send_ytd_ranking_report.assert_awaited_once()

    task2, repository2, reporter2 = _make_task(store=store)
    await task2._on_market_closed("20260717")

    repository2.get_ytd_return_ranking.assert_not_awaited()
    reporter2.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_send_is_not_marked_complete():
    task, _, reporter = _make_task()
    reporter.send_ytd_ranking_report.return_value = False

    await task._on_market_closed("20260717")

    assert task.get_progress()["last_reported_date"] is None


@pytest.mark.asyncio
async def test_empty_ranking_is_not_sent_or_marked_complete():
    task, _, reporter = _make_task(rows=[])

    await task._on_market_closed("20260717")

    reporter.send_ytd_ranking_report.assert_not_awaited()
    assert task.get_progress()["last_reported_date"] is None
