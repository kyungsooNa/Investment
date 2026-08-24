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
        self._claims = set()

    def save_keyed(self, key, value):
        self._data[key] = value

    def load_keyed(self, key):
        return self._data.get(key)

    def claim_daily_task(self, task_name, date_str):
        key = (task_name, date_str)
        if key in self._claims:
            return False
        self._claims.add(key)
        return True

    def release_daily_task(self, task_name, date_str):
        self._claims.discard((task_name, date_str))


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
async def test_claimed_date_skips_without_build_or_send(service, reporter):
    """다른 프로세스가 같은 날짜를 선점했으면 리포트 생성 전 스킵한다."""
    store = _FakeStore()
    assert store.claim_daily_task("market_cap_gap_report_us", "20260625") is True

    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        scheduler_store=store,
        logger=MagicMock(),
    )

    await task._on_market_closed("20260625")

    service.build_report.assert_not_called()
    reporter.send_market_cap_gap_report.assert_not_called()
    assert task.get_progress()["last_reported_date"] is None


@pytest.mark.asyncio
async def test_failed_report_releases_claim_for_retry(service, reporter):
    service.build_report.side_effect = [RuntimeError("boom"), {
        "report_date": "20260625",
        "trigger": "us_close",
        "fx_rate": 1400.0,
        "korean": [],
        "us": [],
        "comparisons": [],
    }]
    store = _FakeStore()
    task = MarketCapGapReportTask(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="us_close",
        scheduler_store=store,
        logger=MagicMock(),
    )

    await task._on_market_closed("20260625")
    await task._on_market_closed("20260625")

    assert service.build_report.await_count == 2
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


def _task(service, reporter, **overrides):
    kwargs = dict(
        market_cap_gap_service=service,
        telegram_reporter=reporter,
        session="kr_close",
        logger=MagicMock(),
    )
    kwargs.update(overrides)
    return MarketCapGapReportTask(**kwargs)


def test_unknown_session_is_rejected(service, reporter):
    with pytest.raises(ValueError, match="kr_close 또는 us_close"):
        _task(service, reporter, session="jp_close")


def test_scheduler_label_carries_the_session(service, reporter):
    assert _task(service, reporter)._scheduler_label == "MarketCapGapReport:kr_close"
    assert (
        _task(service, reporter, session="us_close")._scheduler_label
        == "MarketCapGapReport:us_close"
    )


def test_state_helpers_are_noops_without_a_scheduler_store(service, reporter):
    task = _task(service, reporter, scheduler_store=None)

    assert task._load_last_reported_date() is None
    task._save_last_reported_date("20260625")
    assert task._claim_report_date("20260625") is True
    task._release_report_date_claim("20260625")


def test_state_helpers_absorb_scheduler_store_failures(service, reporter):
    store = MagicMock()
    store.load_keyed.side_effect = RuntimeError("로드 실패")
    store.save_keyed.side_effect = RuntimeError("저장 실패")
    store.claim_daily_task.side_effect = RuntimeError("claim 실패")
    store.release_daily_task.side_effect = RuntimeError("release 실패")
    task = _task(service, reporter, scheduler_store=store)

    assert task._load_last_reported_date() is None
    task._save_last_reported_date("20260625")
    # claim 실패 시에는 보수적으로 진행을 허용한다(리포트 누락 방지).
    assert task._claim_report_date("20260625") is True
    task._release_report_date_claim("20260625")

    # 생성자에서의 초기 로드 실패까지 포함해 모든 실패가 warning 으로 남는다.
    assert task._logger.warning.call_count >= 4


def test_claim_helpers_tolerate_stores_without_daily_task_support(service, reporter):
    store = MagicMock(spec=["load_keyed", "save_keyed"])
    task = _task(service, reporter, scheduler_store=store)

    assert task._claim_report_date("20260625") is True
    task._release_report_date_claim("20260625")


@pytest.mark.asyncio
async def test_success_emits_an_info_notification(service, reporter):
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    task = _task(service, reporter, notification_service=notifier)

    await task._on_market_closed("20260625")

    notifier.emit.assert_awaited_once()
    assert "전송 완료" in notifier.emit.await_args.args[2]


@pytest.mark.asyncio
async def test_failure_emits_an_error_notification(service, reporter):
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    service.build_report = AsyncMock(side_effect=RuntimeError("시총 조회 실패"))
    task = _task(service, reporter, notification_service=notifier)

    await task._on_market_closed("20260625")

    notifier.emit.assert_awaited_once()
    assert task.get_progress()["last_reported_date"] != "20260625"
