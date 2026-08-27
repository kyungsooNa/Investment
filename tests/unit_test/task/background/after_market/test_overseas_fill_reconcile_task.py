"""미국장 체결 대사 자동 실행 태스크 테스트 (P1-3).

`OverseasFillReconcileService` 는 있었지만 수동 라우트에서만 호출돼, 사람이 누르지
않으면 원장이 틀린 채로 쌓였다. 마감 후 하루 1회 자동 실행한다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.overseas_types import OverseasExchange
from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import TaskPriority
from task.background.after_market.overseas_fill_reconcile_task import (
    OverseasFillReconcileTask,
)


def _ok(counts=None, checked=3):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="해외 체결 대사 완료",
        data={"exchange": "NASD", "applied": True, "checked": checked,
              "counts": counts or {"ok": 3, "unfilled": 0, "partial": 0, "unknown": 0},
              "diffs": []},
    )


def _task(resp=None, *, side_effect=None):
    service = MagicMock()
    if side_effect is not None:
        service.reconcile = AsyncMock(side_effect=side_effect)
    else:
        service.reconcile = AsyncMock(return_value=resp if resp is not None else _ok())
    notif = AsyncMock()
    task = OverseasFillReconcileTask(
        reconcile_service=service,
        market_calendar_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
        notification_service=notif,
    )
    return task, service, notif


def test_task_metadata():
    task, _, _ = _task()
    assert task.task_name == "overseas_fill_reconcile"
    assert task._scheduler_label == "OverseasFillReconcile"
    assert task.priority == TaskPriority.LOW


def test_triggers_after_dryrun_in_ny_timezone():
    """dry-run(16:30 ET) 뒤에 돌려 체결내역 반영 시간을 준다."""
    task, _, _ = _task()
    assert task._loop_timezone == "America/New_York"
    assert (task._loop_cron_hour, task._loop_cron_minute) == (17, 0)


@pytest.mark.asyncio
async def test_runs_reconcile_with_apply_for_the_trading_date():
    task, service, _ = _task()

    await task._on_market_closed("20260818")

    kwargs = service.reconcile.await_args.kwargs
    assert kwargs["start_date"] == "20260818"
    assert kwargs["end_date"] == "20260818"
    assert kwargs["exchange"] == OverseasExchange.NASD.value
    assert kwargs["apply"] is True
    assert task.get_progress()["last_run_date"] == "20260818"


@pytest.mark.asyncio
async def test_dedup_same_trading_date():
    task, service, _ = _task()
    await task._on_market_closed("20260818")
    await task._on_market_closed("20260818")
    assert service.reconcile.await_count == 1


@pytest.mark.asyncio
async def test_clean_result_does_not_alert():
    task, _, notif = _task()
    await task._on_market_closed("20260818")
    notif.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrections_are_alerted_with_counts():
    task, _, notif = _task(_ok(counts={"ok": 1, "unfilled": 2, "partial": 1, "unknown": 0}))

    await task._on_market_closed("20260818")

    notif.emit.assert_awaited_once()
    body = notif.emit.await_args.args[3]
    assert "미체결: 2건" in body
    assert "부분체결: 1건" in body


@pytest.mark.asyncio
async def test_unknown_verdicts_are_surfaced():
    """판정 불가는 무조작이라 사람이 봐야 한다."""
    task, _, notif = _task(_ok(counts={"ok": 0, "unfilled": 1, "partial": 0, "unknown": 2}))
    await task._on_market_closed("20260818")
    assert "판정 불가: 2건" in notif.emit.await_args.args[3]


@pytest.mark.asyncio
async def test_query_failure_does_not_mark_date_done():
    """조회 실패로 넘어가면 그날 원장이 영영 보정되지 않는다 — 재시도돼야 한다."""
    failed = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="체결내역 조회 실패", data=None)
    task, service, notif = _task(failed)

    await task._on_market_closed("20260818")

    assert task.get_progress()["last_run_date"] is None
    notif.emit.assert_awaited_once()

    service.reconcile = AsyncMock(return_value=_ok())
    await task._on_market_closed("20260818")
    assert task.get_progress()["last_run_date"] == "20260818"


@pytest.mark.asyncio
async def test_exception_is_absorbed_and_retried():
    task, service, notif = _task(side_effect=RuntimeError("boom"))

    await task._on_market_closed("20260818")

    assert task.get_progress()["last_run_date"] is None
    task._logger.error.assert_called()
    notif.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_works_without_notification_service():
    service = MagicMock()
    service.reconcile = AsyncMock(return_value=_ok(counts={"unfilled": 1}))
    task = OverseasFillReconcileTask(
        reconcile_service=service, market_clock=MagicMock(), logger=MagicMock(),
    )
    await task._on_market_closed("20260818")
    assert task.get_progress()["last_run_date"] == "20260818"
