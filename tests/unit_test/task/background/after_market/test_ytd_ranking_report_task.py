import asyncio
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


@pytest.mark.asyncio
async def test_startup_recovers_unsent_last_trading_day_report():
    rows = [{
        "code": "005930",
        "name": "삼성전자",
        "current_price": 75000,
        "base_price": 50000,
        "base_date": "20260102",
        "latest_date": "20260716",
        "ytd_return_rate": 50.0,
    }]
    task, repository, reporter = _make_task(next_open="20260720", rows=rows)

    await task._on_start_hook()
    await asyncio.gather(*task._tasks)

    repository.get_ytd_return_ranking.assert_awaited_once_with(limit=20)
    reporter.send_ytd_ranking_report.assert_awaited_once_with(rows, "20260716")
    assert task.get_progress()["last_reported_date"] == "20260716"


@pytest.mark.asyncio
async def test_startup_recovery_skips_current_week_snapshot():
    rows = [{
        "code": "005930",
        "name": "삼성전자",
        "latest_date": "20260715",
    }]
    task, _, reporter = _make_task(next_open="20260716", rows=rows)

    await task._recover_missed_report()

    reporter.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_recovery_skips_already_reported_week():
    store = _FakeStore()
    store.save_keyed(YtdRankingReportTask._STATE_KEY, "20260716")
    rows = [{
        "code": "005930",
        "name": "삼성전자",
        "latest_date": "20260716",
    }]
    task, repository, reporter = _make_task(
        next_open="20260720",
        rows=rows,
        store=store,
    )

    await task._recover_missed_report()

    repository.get_ytd_return_ranking.assert_awaited_once_with(limit=20)
    reporter.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_recovery_and_close_execution_do_not_send_duplicate():
    task, _, reporter = _make_task(next_open="20260720")
    rows = [{"code": "005930", "latest_date": "20260717"}]

    async def delayed_send(*_args):
        await asyncio.sleep(0)
        return True

    reporter.send_ytd_ranking_report.side_effect = delayed_send

    await asyncio.gather(
        task._send_report(rows, "20260717"),
        task._send_report(rows, "20260717"),
    )

    reporter.send_ytd_ranking_report.assert_awaited_once()


def test_scheduler_label_and_task_name_are_stable():
    task, _, _ = _make_task()

    assert task.task_name == "ytd_ranking_report"
    assert task._scheduler_label == "YtdRankingReportTask"


def test_state_helpers_are_noops_without_a_scheduler_store():
    task, _, _ = _make_task(store=None)

    assert task._load_last_reported_date() is None
    task._save_last_reported_date("20260717")


def test_state_helpers_absorb_scheduler_store_failures():
    store = MagicMock()
    store.load_keyed.side_effect = RuntimeError("로드 실패")
    store.save_keyed.side_effect = RuntimeError("저장 실패")
    task, _, _ = _make_task(store=store)

    assert task._load_last_reported_date() is None
    task._save_last_reported_date("20260717")

    assert task._logger.warning.call_count >= 2


def test_already_reported_check_tolerates_a_corrupt_saved_date():
    task, _, _ = _make_task()
    task._last_reported_date = "날짜아님"

    assert task._already_reported_this_week("20260717") is False


@pytest.mark.asyncio
async def test_without_a_calendar_service_only_friday_counts_as_week_end():
    task, _, _ = _make_task()
    task._mcs = None

    assert await task._is_last_trading_day_of_week("20260717") is True   # 금요일
    assert await task._is_last_trading_day_of_week("20260716") is False  # 목요일


@pytest.mark.asyncio
@pytest.mark.parametrize("next_open", ["", None, "20260717"])
async def test_unresolvable_next_open_day_blocks_the_report(next_open):
    task, _, reporter = _make_task(next_open=next_open)

    await task._on_market_closed("20260717")

    reporter.send_ytd_ranking_report.assert_not_awaited()
    task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_missing_telegram_reporter_skips_with_a_warning():
    task, repository, _ = _make_task()
    task._telegram_reporter = None

    await task._on_market_closed("20260717")

    repository.get_ytd_return_ranking.assert_not_awaited()
    task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_send_failure_is_logged_and_clears_the_running_flag():
    task, repository, _ = _make_task()
    repository.get_ytd_return_ranking = AsyncMock(side_effect=RuntimeError("조회 실패"))

    await task._on_market_closed("20260717")

    task._logger.error.assert_called_once()
    assert task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_recovery_absorbs_repository_errors():
    task, repository, _ = _make_task()
    repository.get_ytd_return_ranking = AsyncMock(side_effect=RuntimeError("조회 실패"))

    await task._recover_missed_report()

    task._logger.error.assert_called_once()
    assert task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_recovery_stops_when_there_is_no_snapshot():
    task, repository, reporter = _make_task(rows=[])

    await task._recover_missed_report()

    reporter.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_stops_when_the_snapshot_has_no_date():
    task, _, reporter = _make_task(rows=[{"code": "005930", "latest_date": ""}])

    await task._recover_missed_report()

    reporter.send_ytd_ranking_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_hook_is_skipped_without_a_telegram_reporter():
    task, _, _ = _make_task()
    task._telegram_reporter = None

    await task._on_start_hook()

    assert task._tasks == []
