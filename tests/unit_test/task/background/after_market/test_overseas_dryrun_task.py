"""해외 dry-run after-market 태스크 테스트 (Phase 3c).

미국장 after-market 스케줄러(16:30 ET 트리거)에 등록해 매일 1회 dry-run 신호를
산출·flush 한다. 주문 경로 없음(서비스에 order 의존 부재).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.overseas_dryrun_task import OverseasDryRunTask
from interfaces.schedulable_task import TaskPriority
from common.overseas_types import OverseasExchange


def _make_task(exchange=OverseasExchange.NASD, signals=None):
    dryrun = MagicMock()
    dryrun.scan_dry_run = AsyncMock(return_value=signals if signals is not None else [
        {"code": "AAA", "action": "BUY", "reason": "vbo_daily_breakout"},
    ])
    journal = MagicMock()
    logger = MagicMock()
    notification_service = AsyncMock()
    task = OverseasDryRunTask(
        dryrun_service=dryrun,
        shadow_journal=journal,
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=logger,
        notification_service=notification_service,
        exchange=exchange,
    )
    return task, dryrun, journal, notification_service, logger


def test_task_metadata():
    task, _, _, _, _ = _make_task()
    assert task.task_name == "overseas_dryrun"
    assert task._scheduler_label == "OverseasDryRun"
    assert task.priority == TaskPriority.LOW


def test_loop_triggers_on_us_market_close():
    """after-market 루프를 미국 정규장 마감(16:00 ET) 직후 NY 타임존에 맞춘다.

    KST 15:41 하드코딩이 아니라 America/New_York 16:30 으로 트리거되도록
    AfterMarketLoop 파라미터 훅을 오버라이드한다.
    """
    task, _, _, _, _ = _make_task()
    assert task._loop_timezone == "America/New_York"
    assert task._loop_cron_hour == 16
    assert task._loop_cron_minute == 30


@pytest.mark.asyncio
async def test_on_market_closed_runs_scan_and_flushes():
    task, dryrun, journal, notification_service, logger = _make_task(OverseasExchange.NASD)

    await task._on_market_closed("20260706")

    dryrun.scan_dry_run.assert_awaited_once()
    args, kwargs = dryrun.scan_dry_run.await_args
    assert (kwargs.get("exchange") == OverseasExchange.NASD) or (args and args[0] == OverseasExchange.NASD)
    journal.flush_to_file.assert_called_once_with("20260706")
    logger.info.assert_any_call(
        {
            "event": "overseas_dryrun_done",
            "market_date": "20260706",
            "market_date_text": "2026-07-06",
            "exchange": "NASD",
            "signals": 1,
            "summary": {"VBO": 1},
        }
    )
    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.INFO,
        "해외 dry-run 완료",
        "미국 거래일 2026-07-06 기준 dry-run 리포트: 총 1개 신호\n- VBO: 1개 (AAA)",
    )


@pytest.mark.asyncio
async def test_on_market_closed_notification_groups_vbo_pp_bgu_cb_rsi2_osb_signals():
    signals = [
        {"code": "AAA", "action": "BUY", "reason": "vbo_daily_breakout"},
        {"code": "BBB", "name": "Beta", "strategy": "O'NeilPP_overseas", "action": "BUY"},
        {"code": "CCC", "strategy": "O'NeilPP_overseas", "action": "BUY"},
        {"code": "DDD", "strategy": "O'NeilBGU_overseas", "action": "BUY"},
        {"code": "EEE", "strategy": "LarryWilliamsCB_overseas", "action": "BUY"},
        {"code": "FFF", "strategy": "RSI2Pullback_overseas", "action": "BUY"},
        {"code": "GGG", "strategy": "O'NeilOSB_overseas", "action": "BUY"},
    ]
    task, _, _, notification_service, logger = _make_task(signals=signals)

    await task._on_market_closed("20260706")

    logger.info.assert_any_call(
        {
            "event": "overseas_dryrun_done",
            "market_date": "20260706",
            "market_date_text": "2026-07-06",
            "exchange": "NASD",
            "signals": 7,
            "summary": {"VBO": 1, "PP": 2, "BGU": 1, "CB": 1, "RSI2": 1, "OSB": 1},
        }
    )
    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.INFO,
        "해외 dry-run 완료",
        (
            "미국 거래일 2026-07-06 기준 dry-run 리포트: 총 7개 신호\n"
            "- VBO: 1개 (AAA)\n"
            "- PP: 2개 (Beta, CCC)\n"
            "- BGU: 1개 (DDD)\n"
            "- CB: 1개 (EEE)\n"
            "- RSI2: 1개 (FFF)\n"
            "- OSB: 1개 (GGG)"
        ),
    )


@pytest.mark.asyncio
async def test_dedup_same_date_skips_second_run():
    task, dryrun, journal, _, logger = _make_task()

    await task._on_market_closed("20260615")
    await task._on_market_closed("20260615")

    assert dryrun.scan_dry_run.await_count == 1
    logger.info.assert_any_call(
        {
            "event": "overseas_dryrun_skip",
            "market_date": "20260615",
            "market_date_text": "2026-06-15",
            "exchange": "NASD",
            "reason": "already_run",
            "reason_text": "이미 처리한 미국 거래일이므로 dry-run을 스킵합니다.",
        }
    )


@pytest.mark.asyncio
async def test_failure_does_not_mark_date_done_allowing_retry():
    task, dryrun, journal, _, _ = _make_task()
    dryrun.scan_dry_run = AsyncMock(side_effect=RuntimeError("boom"))

    await task._on_market_closed("20260615")  # 예외 삼킴
    dryrun.scan_dry_run = AsyncMock(return_value=[])
    await task._on_market_closed("20260615")  # 재시도 → 실행됨

    assert dryrun.scan_dry_run.await_count == 1  # 두번째 mock 기준 1회


def test_task_has_no_order_dependency():
    task, _, _, _, _ = _make_task()
    assert not hasattr(task, "_order_execution_service")
    assert not hasattr(task, "_order_service")


def _make_task_with_report(signals, run_report):
    """suite 의 last_run_report 를 노출하는 dry-run 서비스 대역으로 태스크를 만든다."""
    dryrun = MagicMock()
    dryrun.scan_dry_run = AsyncMock(return_value=signals)
    dryrun.last_run_report = run_report
    notification_service = AsyncMock()
    logger = MagicMock()
    task = OverseasDryRunTask(
        dryrun_service=dryrun,
        shadow_journal=MagicMock(),
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=logger,
        notification_service=notification_service,
        exchange=OverseasExchange.NASD,
    )
    return task, notification_service, logger


@pytest.mark.asyncio
async def test_notification_lists_strategies_that_ran_with_zero_signals():
    """0건도 리포트에 남긴다 — 빠진 전략이 '설정 없음'인지 '신호 없음'인지 구분 불가였다."""
    task, notification_service, logger = _make_task_with_report(
        signals=[{"code": "AAA", "reason": "vbo_daily_breakout"}],
        run_report=[
            {"strategy": "LarryWilliamsVBO_overseas", "ok": True, "signals": 1, "error": None},
            {"strategy": "O'NeilPP_overseas", "ok": True, "signals": 0, "error": None},
        ],
    )

    await task._on_market_closed("20260706")

    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.INFO,
        "해외 dry-run 완료",
        (
            "미국 거래일 2026-07-06 기준 dry-run 리포트: 총 1개 신호\n"
            "- VBO: 1개 (AAA)\n"
            "- PP: 0개"
        ),
    )
    logger.info.assert_any_call(
        {
            "event": "overseas_dryrun_done",
            "market_date": "20260706",
            "market_date_text": "2026-07-06",
            "exchange": "NASD",
            "signals": 1,
            "summary": {"VBO": 1, "PP": 0},
        }
    )


@pytest.mark.asyncio
async def test_notification_marks_failed_strategy_and_escalates_level():
    """죽은 전략은 0건과 다르게 보여야 하고, 알림 레벨도 올라가야 한다."""
    task, notification_service, logger = _make_task_with_report(
        signals=[{"code": "AAA", "reason": "vbo_daily_breakout"}],
        run_report=[
            {"strategy": "LarryWilliamsVBO_overseas", "ok": True, "signals": 1, "error": None},
            {"strategy": "O'NeilPP_overseas", "ok": False, "signals": 0, "error": "boom"},
        ],
    )

    await task._on_market_closed("20260706")

    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.WARNING,
        "해외 dry-run 완료 (일부 전략 실패)",
        (
            "미국 거래일 2026-07-06 기준 dry-run 리포트: 총 1개 신호\n"
            "- VBO: 1개 (AAA)\n"
            "- PP: 실행 실패 (boom)"
        ),
    )
    logger.warning.assert_any_call(
        {
            "event": "overseas_dryrun_strategy_failed",
            "market_date": "20260706",
            "exchange": "NASD",
            "strategy": "O'NeilPP_overseas",
            "error": "boom",
        }
    )


@pytest.mark.asyncio
async def test_run_report_without_strategy_names_falls_back_to_signal_summary():
    """리포트를 못 주는 서비스(구형 대역)는 기존 신호 기반 요약을 그대로 쓴다."""
    task, notification_service, _ = _make_task_with_report(
        signals=[{"code": "AAA", "reason": "vbo_daily_breakout"}],
        run_report=[],
    )

    await task._on_market_closed("20260706")

    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.INFO,
        "해외 dry-run 완료",
        "미국 거래일 2026-07-06 기준 dry-run 리포트: 총 1개 신호\n- VBO: 1개 (AAA)",
    )


@pytest.mark.asyncio
async def test_signal_label_missing_from_run_report_is_still_listed():
    """리포트에 없는 라벨(기타 등)의 신호도 누락하지 않는다."""
    task, notification_service, _ = _make_task_with_report(
        signals=[
            {"code": "AAA", "reason": "vbo_daily_breakout"},
            {"code": "ZZZ", "strategy": "Unknown_overseas"},
        ],
        run_report=[
            {"strategy": "LarryWilliamsVBO_overseas", "ok": True, "signals": 1, "error": None},
        ],
    )

    await task._on_market_closed("20260706")

    body = notification_service.emit.await_args[0][3]
    assert "- VBO: 1개 (AAA)" in body
    assert "- 기타: 1개 (ZZZ)" in body


# ── 시장 국면 라벨 (기록 전용) ───────────────────────────────────────────

def _regime(*, label="bull", error=None):
    svc = MagicMock()
    svc.MARKET = "US"
    if error is not None:
        svc.classify = AsyncMock(side_effect=error)
    else:
        svc.classify = AsyncMock(return_value=MagicMock(regime_label=label))
    return svc


def _make_task_with_regime(regime):
    dryrun = MagicMock()
    dryrun.scan_dry_run = AsyncMock(return_value=[
        {"code": "AAA", "action": "BUY", "reason": "vbo_daily_breakout"},
    ])
    notification_service = AsyncMock()
    task = OverseasDryRunTask(
        dryrun_service=dryrun,
        shadow_journal=MagicMock(),
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
        notification_service=notification_service,
        market_regime_service=regime,
    )
    return task, dryrun, notification_service


@pytest.mark.asyncio
async def test_regime_label_is_recorded_in_notification_and_log():
    regime = _regime(label="bear")
    task, dryrun, notif = _make_task_with_regime(regime)

    await task._on_market_closed("20260706")

    # 라벨은 기록만 — 스캔은 국면과 무관하게 실행된다
    dryrun.scan_dry_run.assert_awaited_once()
    body = notif.emit.await_args.args[3]
    assert "시장 국면: bear" in body
    logged = [c.args[0] for c in task._logger.info.call_args_list
              if isinstance(c.args[0], dict) and c.args[0].get("event") == "overseas_dryrun_done"]
    assert logged and logged[0]["regime"] == "bear"


@pytest.mark.asyncio
async def test_regime_lookup_failure_does_not_block_dryrun():
    """국면 조회 실패가 dry-run 수집을 막아서는 안 된다."""
    task, dryrun, notif = _make_task_with_regime(_regime(error=RuntimeError("조회 실패")))

    await task._on_market_closed("20260706")

    dryrun.scan_dry_run.assert_awaited_once()
    notif.emit.assert_awaited_once()
    assert "시장 국면" not in notif.emit.await_args.args[3]
    task._logger.warning.assert_called()


@pytest.mark.asyncio
async def test_without_regime_service_notification_is_unchanged():
    task, _, _, notif, _ = _make_task()

    await task._on_market_closed("20260706")

    assert "시장 국면" not in notif.emit.await_args.args[3]
