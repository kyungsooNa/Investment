"""해외 VBO dry-run after-market 태스크 테스트 (Phase 3c).

미국장 after-market 스케줄러(16:30 ET 트리거)에 등록해 매일 1회 dry-run 신호를
산출·flush 한다. 주문 경로 없음(서비스에 order 의존 부재).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.overseas_dryrun_task import OverseasDryRunTask
from interfaces.schedulable_task import TaskPriority
from common.overseas_types import OverseasExchange


def _make_task(exchange=OverseasExchange.NASD):
    dryrun = MagicMock()
    dryrun.scan_dry_run = AsyncMock(return_value=[{"code": "AAA", "action": "BUY"}])
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
    assert task.task_name == "overseas_vbo_dryrun"
    assert task._scheduler_label == "OverseasVBODryRun"
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
        }
    )
    notification_service.emit.assert_awaited_once_with(
        NotificationCategory.BACKGROUND,
        NotificationLevel.INFO,
        "해외 VBO dry-run 완료",
        "미국 거래일 2026-07-06 기준 dry-run 리포트: 1개 신호",
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
