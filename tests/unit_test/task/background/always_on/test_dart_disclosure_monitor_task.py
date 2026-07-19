from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from repositories.dart_disclosure_repository import StoredDisclosure
from services.dart_disclosure_client import DartDisclosure, DartDisclosurePage
from services.dart_disclosure_rule_service import DisclosureImportance
from task.background.always_on.dart_disclosure_monitor_task import DartDisclosureMonitorTask


class _Clock:
    def __init__(self, now: datetime):
        self.now = now

    def get_current_kst_time(self):
        return self.now


def _disclosure(code="005930", receipt_no="20260714000001", report_name="전환사채권발행결정"):
    return DartDisclosure(
        corp_class="Y",
        corp_name="삼성전자",
        corp_code="00126380",
        stock_code=code,
        report_name=report_name,
        receipt_no=receipt_no,
        filer_name="삼성전자",
        receipt_date="20260714",
        remarks="유",
    )


def _make_task(items, *, initialized=True, now=None, ai_analyzer=None):
    client = MagicMock()
    client.fetch_disclosures = AsyncMock(
        return_value=DartDisclosurePage(items, 1, 100, len(items), 1)
    )
    repo = MagicMock()
    repo.is_initialized = AsyncMock(return_value=initialized)
    repo.mark_initialized = AsyncMock()
    repo.has_receipt = AsyncMock(return_value=False)
    repo.get_known_receipt_nos = AsyncMock(return_value=set())
    repo.save_detected = AsyncMock(return_value=True)
    repo.get_pending_immediate = AsyncMock(return_value=[])
    repo.mark_immediate_sent = AsyncMock()
    repo.increment_send_retry = AsyncMock()
    repo.get_pending_digest = AsyncMock(return_value=[])
    repo.mark_digest_sent = AsyncMock()
    favorites = MagicMock()
    favorites.get_all = AsyncMock(return_value=["005930"])
    rule_service = MagicMock()
    rule_service.evaluate.return_value = DisclosureImportance(
        score=85, level="HIGH", reasons=["자금조달·주식 희석 관련 공시"]
    )
    reporter = MagicMock()
    reporter.send_disclosure_alert = AsyncMock(return_value=True)
    reporter.send_disclosure_digest = AsyncMock(return_value=True)
    config = SimpleNamespace(
        poll_interval_sec=300,
        off_hours_interval_sec=1800,
        active_start_time="07:00",
        active_end_time="19:30",
        immediate_alert_score=70,
        daily_digest_enabled=True,
        daily_digest_time="19:40",
        max_pages_per_poll=5,
    )
    task = DartDisclosureMonitorTask(
        client=client,
        repository=repo,
        favorite_repository=favorites,
        rule_service=rule_service,
        telegram_reporter=reporter,
        config=config,
        market_clock=_Clock(now or datetime(2026, 7, 14, 10, 0, 0)),
        logger=MagicMock(),
        ai_analyzer=ai_analyzer,
    )
    return SimpleNamespace(
        task=task,
        client=client,
        repo=repo,
        favorites=favorites,
        rules=rule_service,
        reporter=reporter,
        ai_analyzer=ai_analyzer,
    )


async def test_first_tick_baselines_matching_disclosures_without_alert_flood():
    deps = _make_task([_disclosure(), _disclosure(code="000660")], initialized=False)

    await deps.task._tick()

    deps.repo.save_detected.assert_awaited_once()
    assert deps.repo.save_detected.await_args.kwargs["suppress_immediate"] is True
    deps.repo.mark_initialized.assert_awaited_once()
    deps.reporter.send_disclosure_alert.assert_not_awaited()
    deps.reporter.send_disclosure_digest.assert_awaited_once()
    deps.repo.mark_digest_sent.assert_awaited_once()


async def test_new_favorite_disclosure_is_saved_and_immediately_reported():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["자금조달·주식 희석 관련 공시"])
    deps = _make_task([disclosure], initialized=True)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    deps.repo.save_detected.assert_awaited_once()
    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure, importance, ai_summary=None
    )
    deps.repo.mark_immediate_sent.assert_awaited_once()
    assert deps.task.get_progress()["sent_count"] == 1


async def test_ai_summary_is_attached_to_immediate_alert_when_analyzer_present():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["자금조달·주식 희석 관련 공시"])
    analyzer = MagicMock()
    analyzer.summarize = AsyncMock(return_value="전환사채 발행 요약")
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    analyzer.summarize.assert_awaited_once_with(disclosure, importance)
    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure, importance, ai_summary="전환사채 발행 요약"
    )


async def test_ai_analyzer_failure_falls_back_to_rule_only_alert():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    analyzer = MagicMock()
    analyzer.summarize = AsyncMock(return_value=None)  # 폴백 신호
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure, importance, ai_summary=None
    )
    deps.repo.mark_immediate_sent.assert_awaited_once()


async def test_non_favorite_disclosure_is_ignored():
    deps = _make_task([_disclosure(code="000660")])

    await deps.task._tick()

    deps.repo.save_detected.assert_not_awaited()
    deps.rules.evaluate.assert_not_called()


async def test_empty_favorites_skip_external_request():
    deps = _make_task([_disclosure()])
    deps.favorites.get_all.return_value = []

    await deps.task._tick()

    deps.client.fetch_disclosures.assert_not_awaited()


async def test_failed_telegram_send_remains_pending_for_retry():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    deps = _make_task([disclosure])
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]
    deps.reporter.send_disclosure_alert.return_value = False

    await deps.task._tick()

    deps.repo.mark_immediate_sent.assert_not_awaited()
    deps.repo.increment_send_retry.assert_awaited_once_with(disclosure.receipt_no)


async def test_digest_is_sent_once_at_configured_time():
    disclosure = _disclosure(report_name="분기보고서 (2026.03)")
    importance = DisclosureImportance(30, "NORMAL", ["정기보고서"])
    deps = _make_task([], now=datetime(2026, 7, 14, 19, 40, 0))
    deps.repo.get_pending_digest.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    deps.reporter.send_disclosure_digest.assert_awaited_once_with(
        [StoredDisclosure(disclosure, importance)], "20260714"
    )
    deps.repo.mark_digest_sent.assert_awaited_once()


def test_task_is_low_priority_and_reports_initial_progress():
    deps = _make_task([])

    assert deps.task.task_name == "dart_disclosure_monitor"
    assert int(deps.task.priority) == 100
    assert deps.task.get_progress()["running"] is False


def test_sleep_interval_wakes_at_digest_time_during_off_hours():
    deps = _make_task([], now=datetime(2026, 7, 14, 19, 35, 0))

    assert deps.task._interval_for(deps.task._market_clock.now) == 300
