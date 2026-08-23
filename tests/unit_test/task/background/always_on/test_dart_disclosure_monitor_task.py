import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from interfaces.schedulable_task import TaskState
from repositories.dart_disclosure_repository import StoredDisclosure
from services.ai_disclosure_analyzer import AiDisclosureAnalysis
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


def _make_task(
    items,
    *,
    initialized=True,
    now=None,
    ai_analyzer=None,
    favorite_codes=None,
    notification_service=None,
):
    client = MagicMock()
    client.fetch_disclosures = AsyncMock(
        return_value=DartDisclosurePage(items, 1, 100, len(items), 1)
    )
    client.fetch_disclosure_text = AsyncMock(return_value="공시 실제 본문")
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
    favorites.get_all = AsyncMock(
        return_value=["005930"] if favorite_codes is None else favorite_codes
    )
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
        notification_service=notification_service,
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


async def test_favorite_codes_are_zero_padded_before_matching_recent_missed_cases():
    samsung = _disclosure(
        code="005930",
        receipt_no="20260714000001",
        report_name="전환사채권발행결정",
    )
    hanmi = _disclosure(
        code="042700",
        receipt_no="20260714000002",
        report_name="기업가치제고계획(자율공시)",
    )
    deps = _make_task(
        [samsung, hanmi],
        initialized=True,
        favorite_codes=["5930", "42700"],
    )

    await deps.task._tick()

    assert deps.repo.save_detected.await_count == 2
    saved_codes = {
        call.args[0].stock_code for call in deps.repo.save_detected.await_args_list
    }
    assert saved_codes == {"005930", "042700"}
    assert deps.task.get_progress()["matched_count"] == 2


async def test_ai_summary_is_attached_to_immediate_alert_when_analyzer_present():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["자금조달·주식 희석 관련 공시"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(
        return_value=AiDisclosureAnalysis("전환사채 발행 요약", importance)
    )
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    analyzer.analyze.assert_awaited_once_with(disclosure, importance, "공시 실제 본문")
    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure, importance, ai_summary="전환사채 발행 요약"
    )


async def test_ai_analyzer_failure_falls_back_to_rule_only_alert():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(return_value=None)  # 폴백 신호
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]

    await deps.task._tick()

    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure, importance, ai_summary=None
    )
    deps.repo.mark_immediate_sent.assert_awaited_once()


async def test_telegram_retry_reuses_ai_summary_without_second_ai_call():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(
        return_value=AiDisclosureAnalysis("재사용할 요약", importance)
    )
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]
    deps.reporter.send_disclosure_alert.side_effect = [False, True]

    await deps.task._send_pending_immediate(deps.task._market_clock.now)
    await deps.task._send_pending_immediate(deps.task._market_clock.now)

    analyzer.analyze.assert_awaited_once_with(disclosure, importance, "공시 실제 본문")
    assert deps.reporter.send_disclosure_alert.await_count == 2
    for call in deps.reporter.send_disclosure_alert.await_args_list:
        assert call.kwargs["ai_summary"] == "재사용할 요약"


async def test_telegram_retry_reuses_rule_fallback_without_second_ai_call():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(return_value=None)
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]
    deps.reporter.send_disclosure_alert.side_effect = [False, True]

    await deps.task._send_pending_immediate(deps.task._market_clock.now)
    await deps.task._send_pending_immediate(deps.task._market_clock.now)

    analyzer.analyze.assert_awaited_once_with(disclosure, importance, "공시 실제 본문")
    assert deps.reporter.send_disclosure_alert.await_count == 2
    for call in deps.reporter.send_disclosure_alert.await_args_list:
        assert call.kwargs["ai_summary"] is None


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


async def test_failed_telegram_send_emits_operational_alert_for_retry():
    disclosure = _disclosure()
    importance = DisclosureImportance(85, "HIGH", ["중요"])
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    deps = _make_task([disclosure], notification_service=notification_service)
    deps.repo.get_pending_immediate.return_value = [StoredDisclosure(disclosure, importance)]
    deps.reporter.send_disclosure_alert.return_value = False

    await deps.task._tick()

    deps.repo.increment_send_retry.assert_awaited_once_with(disclosure.receipt_no)
    notification_service.emit.assert_awaited_once()
    assert notification_service.emit.await_args.args[2] == "공시 텔레그램 발송 실패"
    assert notification_service.emit.await_args.args[4]["receipt_no"] == disclosure.receipt_no


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


async def test_actual_content_analysis_can_promote_low_title_to_immediate_alert():
    disclosure = _disclosure(report_name="기업가치제고계획(자율공시)")
    preliminary = DisclosureImportance(10, "LOW", ["일반 공시"])
    promoted = DisclosureImportance(75, "HIGH", ["신제품·생산능력·해외진출 계획"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(
        return_value=AiDisclosureAnalysis(
            "하이브리드 본더 공장과 미국 법인 설립을 추진합니다.",
            promoted,
            "기업가치제고|하이브리드본더|2027상반기",
        )
    )
    deps = _make_task([disclosure], initialized=True, ai_analyzer=analyzer)
    deps.rules.evaluate.return_value = preliminary
    deps.repo.get_pending_immediate.return_value = [
        StoredDisclosure(disclosure, promoted)
    ]

    await deps.task._tick()

    deps.client.fetch_disclosure_text.assert_awaited_once_with(disclosure.receipt_no)
    analyzer.analyze.assert_awaited_once_with(
        disclosure, preliminary, "공시 실제 본문"
    )
    assert deps.repo.save_detected.await_args.args[1] == promoted
    assert (
        deps.repo.save_detected.await_args.kwargs["event_key"]
        == "기업가치제고|하이브리드본더|2027상반기"
    )
    assert (
        deps.repo.save_detected.await_args.kwargs["summary"]
        == "하이브리드 본더 공장과 미국 법인 설립을 추진합니다."
    )
    deps.reporter.send_disclosure_alert.assert_awaited_once_with(
        disclosure,
        promoted,
        ai_summary="하이브리드 본더 공장과 미국 법인 설립을 추진합니다.",
    )


# --- 라이프사이클 / 루프 ----------------------------------------------------


async def test_start_is_idempotent_and_stop_clears_running_flag():
    deps = _make_task([])

    await deps.task.start()
    await deps.task.start()
    assert len(deps.task._tasks) == 1
    assert deps.task.state == TaskState.RUNNING
    assert deps.task.get_progress()["running"] is True

    await deps.task.stop()

    assert deps.task.state == TaskState.STOPPED
    assert deps.task._tasks == []
    assert deps.task.get_progress()["running"] is False


async def test_suspend_only_applies_while_running_and_resume_restores_running():
    deps = _make_task([])

    await deps.task.suspend()
    assert deps.task.state == TaskState.IDLE

    deps.task._state = TaskState.RUNNING
    await deps.task.suspend()
    assert deps.task.state == TaskState.SUSPENDED

    await deps.task.resume()
    assert deps.task.state == TaskState.RUNNING

    await deps.task.resume()
    assert deps.task.state == TaskState.RUNNING


async def test_loop_records_tick_error_and_emits_operational_alert():
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    deps = _make_task([], notification_service=notifier)
    deps.task._state = TaskState.RUNNING
    deps.task._tick = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "task.background.always_on.dart_disclosure_monitor_task.asyncio.sleep",
        AsyncMock(side_effect=[asyncio.CancelledError()]),
    ):
        await deps.task._loop()

    assert deps.task.get_progress()["last_error"] == "RuntimeError: boom"
    notifier.emit.assert_awaited_once()


async def test_loop_skips_tick_when_not_running():
    deps = _make_task([])
    deps.task._state = TaskState.SUSPENDED
    deps.task._tick = AsyncMock()

    with patch(
        "task.background.always_on.dart_disclosure_monitor_task.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        await deps.task._loop()

    deps.task._tick.assert_not_awaited()


async def test_tick_is_skipped_while_a_previous_tick_holds_the_lock():
    deps = _make_task([_disclosure()])
    lock = deps.task._get_tick_lock()
    assert deps.task._get_tick_lock() is lock

    await lock.acquire()
    try:
        await deps.task._tick()
    finally:
        lock.release()

    deps.favorites.get_all.assert_not_awaited()


# --- 운영 알림 --------------------------------------------------------------


async def test_operational_alert_is_a_noop_without_notification_service():
    deps = _make_task([])

    await deps.task._emit_operational_alert("제목", "본문")


async def test_operational_alert_failure_is_logged_and_absorbed():
    notifier = MagicMock()
    notifier.emit = AsyncMock(side_effect=RuntimeError("notify down"))
    deps = _make_task([], notification_service=notifier)

    await deps.task._emit_operational_alert("제목", "본문")

    deps.task._logger.error.assert_called_once()


# --- 페이지네이션 -----------------------------------------------------------


async def test_fetch_recent_stops_when_all_receipts_are_already_known():
    deps = _make_task([_disclosure()])
    page = DartDisclosurePage([_disclosure()], 1, 100, 1, 3)
    deps.client.fetch_disclosures = AsyncMock(return_value=page)
    deps.repo.get_known_receipt_nos = AsyncMock(return_value={"20260714000001"})

    collected = await deps.task._fetch_recent("20260714")

    assert len(collected) == 1
    assert deps.client.fetch_disclosures.await_count == 1


async def test_fetch_recent_walks_pages_until_last_page():
    deps = _make_task([_disclosure()])
    first = DartDisclosurePage([_disclosure(receipt_no="1")], 1, 100, 2, 2)
    second = DartDisclosurePage([_disclosure(receipt_no="2")], 2, 100, 2, 2)
    deps.client.fetch_disclosures = AsyncMock(side_effect=[first, second])

    collected = await deps.task._fetch_recent("20260714")

    assert len(collected) == 2
    assert deps.client.fetch_disclosures.await_count == 2


# --- AI 본문 분석 -----------------------------------------------------------


async def test_actual_content_analysis_returns_none_without_analyzer():
    deps = _make_task([_disclosure()])

    result = await deps.task._analyze_actual_content(
        _disclosure(), DisclosureImportance(score=10, level="LOW", reasons=[])
    )

    assert result is None


async def test_actual_content_analysis_returns_none_when_text_lookup_fails():
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock()
    deps = _make_task([_disclosure()], ai_analyzer=analyzer)
    deps.client.fetch_disclosure_text = AsyncMock(side_effect=RuntimeError("본문 없음"))

    result = await deps.task._analyze_actual_content(
        _disclosure(), DisclosureImportance(score=10, level="LOW", reasons=[])
    )

    assert result is None
    analyzer.analyze.assert_not_awaited()
    deps.task._logger.warning.assert_called_once()


async def test_actual_content_analysis_returns_none_for_empty_document():
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock()
    deps = _make_task([_disclosure()], ai_analyzer=analyzer)
    deps.client.fetch_disclosure_text = AsyncMock(return_value="")

    result = await deps.task._analyze_actual_content(
        _disclosure(), DisclosureImportance(score=10, level="LOW", reasons=[])
    )

    assert result is None
    analyzer.analyze.assert_not_awaited()


# --- 중요도 병합 ------------------------------------------------------------


def test_merge_importance_prefers_higher_score_and_unions_reasons_on_tie():
    low = DisclosureImportance(score=40, level="LOW", reasons=["규칙"])
    high = DisclosureImportance(score=80, level="HIGH", reasons=["AI"])

    assert DartDisclosureMonitorTask._merge_importance(low, high) is high
    assert DartDisclosureMonitorTask._merge_importance(high, low) is high

    same_a = DisclosureImportance(score=50, level="MID", reasons=["규칙", "공통"])
    same_b = DisclosureImportance(score=50, level="MID", reasons=["공통", "AI"])
    merged = DartDisclosureMonitorTask._merge_importance(same_a, same_b)
    assert merged.score == 50
    assert merged.reasons == ["규칙", "공통", "AI"]


# --- 다이제스트 / 폴링 간격 --------------------------------------------------


async def test_digest_is_skipped_when_disabled_or_before_configured_time():
    deps = _make_task([])
    deps.task._config.daily_digest_enabled = False

    await deps.task._send_digest_if_due(datetime(2026, 7, 14, 20, 0), "20260714")
    deps.repo.get_pending_digest.assert_not_awaited()

    deps.task._config.daily_digest_enabled = True
    await deps.task._send_digest_if_due(datetime(2026, 7, 14, 10, 0), "20260714")
    deps.repo.get_pending_digest.assert_not_awaited()


async def test_digest_stays_pending_when_send_fails():
    deps = _make_task([])
    pending = [StoredDisclosure(_disclosure(), DisclosureImportance(50, "MID", []))]
    deps.repo.get_pending_digest = AsyncMock(return_value=pending)
    deps.reporter.send_disclosure_digest = AsyncMock(return_value=False)

    await deps.task._send_digest_if_due(datetime(2026, 7, 14, 20, 0), "20260714")

    deps.repo.mark_digest_sent.assert_not_awaited()
    assert deps.task.get_progress()["pending_digest_count"] == 1


def test_off_hours_interval_is_used_outside_the_active_window():
    deps = _make_task([])

    assert deps.task._interval_for(datetime(2026, 7, 14, 3, 0)) == 1800
    assert deps.task._interval_for(datetime(2026, 7, 14, 10, 0)) == 300


def test_interval_ignores_unparsable_digest_time():
    deps = _make_task([])
    deps.task._config.daily_digest_time = "저녁"

    assert deps.task._interval_for(datetime(2026, 7, 14, 10, 0)) == 300


def test_interval_is_not_shortened_after_digest_time():
    deps = _make_task([])

    assert deps.task._interval_for(datetime(2026, 7, 14, 19, 50)) == 1800
