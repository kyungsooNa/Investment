"""해외 VBO dry-run after-market 태스크 테스트 (Phase 3c).

기존 한국장 after-market 스케줄러(KST 트리거)에 등록해 매일 1회 dry-run 신호를
산출·flush 한다. 주문 경로 없음(서비스에 order 의존 부재).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from task.background.after_market.overseas_dryrun_task import OverseasDryRunTask
from interfaces.schedulable_task import TaskPriority
from common.overseas_types import OverseasExchange


def _make_task(exchange=OverseasExchange.NASD):
    dryrun = MagicMock()
    dryrun.scan_dry_run = AsyncMock(return_value=[{"code": "AAA", "action": "BUY"}])
    journal = MagicMock()
    task = OverseasDryRunTask(
        dryrun_service=dryrun,
        shadow_journal=journal,
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
        exchange=exchange,
    )
    return task, dryrun, journal


def test_task_metadata():
    task, _, _ = _make_task()
    assert task.task_name == "overseas_vbo_dryrun"
    assert task._scheduler_label == "OverseasVBODryRun"
    assert task.priority == TaskPriority.LOW


@pytest.mark.asyncio
async def test_on_market_closed_runs_scan_and_flushes():
    task, dryrun, journal = _make_task(OverseasExchange.NASD)

    await task._on_market_closed("20260615")

    dryrun.scan_dry_run.assert_awaited_once()
    args, kwargs = dryrun.scan_dry_run.await_args
    assert (kwargs.get("exchange") == OverseasExchange.NASD) or (args and args[0] == OverseasExchange.NASD)
    journal.flush_to_file.assert_called_once_with("20260615")


@pytest.mark.asyncio
async def test_dedup_same_date_skips_second_run():
    task, dryrun, journal = _make_task()

    await task._on_market_closed("20260615")
    await task._on_market_closed("20260615")

    assert dryrun.scan_dry_run.await_count == 1


@pytest.mark.asyncio
async def test_failure_does_not_mark_date_done_allowing_retry():
    task, dryrun, journal = _make_task()
    dryrun.scan_dry_run = AsyncMock(side_effect=RuntimeError("boom"))

    await task._on_market_closed("20260615")  # 예외 삼킴
    dryrun.scan_dry_run = AsyncMock(return_value=[])
    await task._on_market_closed("20260615")  # 재시도 → 실행됨

    assert dryrun.scan_dry_run.await_count == 1  # 두번째 mock 기준 1회


def test_task_has_no_order_dependency():
    task, _, _ = _make_task()
    assert not hasattr(task, "_order_execution_service")
    assert not hasattr(task, "_order_service")
