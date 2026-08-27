"""미국장 개장 대사 태스크 테스트 (P1-2).

`OverseasReconcileService`(로컬 포지션 vs 브로커 잔고 drift 비교)는 이미 있었으나
아무도 호출하지 않았다. 개장 직후 1회 돌려 drift 를 알린다.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.overseas_opening_reconcile_task import (
    OverseasOpeningReconcileTask,
)

NY = pytz.timezone("America/New_York")


def _at(h, m):
    return NY.localize(datetime(2026, 8, 18, h, m))


def _strategy(positions):
    svc = MagicMock()
    svc.STRATEGY_NAME = "S"
    svc.get_state = MagicMock(return_value={"positions": positions})
    return svc


def _task(*, strategies=None, now=None, trading_day=True, drift=None, balance_ok=True):
    clock = MagicMock()
    clock.get_current_kst_time = MagicMock(return_value=now or _at(9, 35))
    clock.get_current_kst_date_str = MagicMock(return_value="20260818")
    clock.get_market_open_time = MagicMock(return_value=_at(9, 30))

    us_mcs = MagicMock()
    us_mcs.is_trading_day = MagicMock(return_value=trading_day)

    broker = MagicMock()
    broker.get_overseas_balance = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value if balance_ok else ErrorCode.API_ERROR.value,
        msg1="ok", data={}))

    reconcile_service = MagicMock()
    reconcile_service.reconcile = MagicMock(return_value=drift or {
        "ok": True, "matched": ["AAA"], "missing_in_broker": [],
        "extra_in_broker": [], "qty_mismatch": [], "broker_positions": {"AAA": 3},
    })

    notification_service = AsyncMock()

    task = OverseasOpeningReconcileTask(
        reconcile_service=reconcile_service,
        strategy_services=strategies if strategies is not None else [_strategy({"AAA": {"qty": 3}})],
        broker=broker,
        market_clock=clock,
        us_market_calendar_service=us_mcs,
        notification_service=notification_service,
        logger=MagicMock(),
    )
    return task, reconcile_service, notification_service, broker


# ── 실행 조건 ────────────────────────────────────────────────────────────

def test_task_metadata():
    task, _, _, _ = _task()
    assert task.task_name == "overseas_opening_reconcile"
    assert task.priority == TaskPriority.HIGH


@pytest.mark.asyncio
async def test_runs_once_in_opening_window():
    task, svc, _, _ = _task(now=_at(9, 35))

    await task._tick()
    await task._tick()

    assert svc.reconcile.call_count == 1


@pytest.mark.asyncio
async def test_does_not_run_before_open_delay():
    task, svc, _, _ = _task(now=_at(9, 30))
    await task._tick()
    svc.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_run_after_window_closes():
    task, svc, _, _ = _task(now=_at(11, 0))
    await task._tick()
    svc.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_skips_us_holiday():
    task, svc, _, _ = _task(trading_day=False)
    await task._tick()
    svc.reconcile.assert_not_called()


# ── 대사 입력 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_positions_are_summed_across_strategies():
    """브로커 잔고는 전략을 구분하지 않는다 — 같은 심볼은 합산해야 drift 오탐이 없다."""
    strategies = [
        _strategy({"AAA": {"qty": 3}, "BBB": {"qty": 1}}),
        _strategy({"AAA": {"qty": 2}}),
    ]
    task, svc, _, _ = _task(strategies=strategies)

    await task._tick()

    local = svc.reconcile.call_args.args[0]
    assert local == {"AAA": 5, "BBB": 1}


@pytest.mark.asyncio
async def test_no_positions_still_reconciles():
    """로컬이 비어 있어도 브로커에만 있는 포지션(extra)을 잡아야 한다."""
    task, svc, _, _ = _task(strategies=[_strategy({})])
    await task._tick()
    assert svc.reconcile.call_args.args[0] == {}


# ── 결과 처리 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_reconcile_does_not_alert():
    task, _, notif, _ = _task()
    await task._tick()
    notif.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_drift_emits_warning_alert():
    drift = {
        "ok": True, "matched": [], "missing_in_broker": [{"symbol": "AAA", "local_qty": 3}],
        "extra_in_broker": [], "qty_mismatch": [], "broker_positions": {},
    }
    task, _, notif, _ = _task(drift=drift)

    await task._tick()

    notif.emit.assert_awaited_once()
    body = notif.emit.await_args.args[3]
    assert "AAA" in body
    assert task.get_progress()["last_result"]["drift_count"] == 1


@pytest.mark.asyncio
async def test_balance_query_failure_is_not_treated_as_drift():
    """조회 불가 != 미보유 — 실패를 drift 로 단정하면 매일 오탐 알림이 온다."""
    drift = {
        "ok": False, "error": "balance_query_failed", "matched": [],
        "missing_in_broker": [], "extra_in_broker": [], "qty_mismatch": [],
        "broker_positions": {},
    }
    task, _, notif, _ = _task(drift=drift)

    await task._tick()

    assert task.get_progress()["last_result"]["error"] == "balance_query_failed"
    body = notif.emit.await_args.args[3]
    assert "조회" in body or "실패" in body


@pytest.mark.asyncio
async def test_exception_does_not_mark_date_done_so_it_retries():
    task, svc, _, _ = _task()
    svc.reconcile = MagicMock(side_effect=RuntimeError("boom"))

    await task._tick()
    assert task._last_checked_date is None

    svc.reconcile = MagicMock(return_value={
        "ok": True, "matched": [], "missing_in_broker": [], "extra_in_broker": [],
        "qty_mismatch": [], "broker_positions": {}})
    await task._tick()
    assert task._last_checked_date == "20260818"


# ── 라이프사이클 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_stop_cancels_loop():
    task, _, _, _ = _task()
    await task.start()
    assert task.state == TaskState.IDLE
    await task.stop()
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_loop_absorbs_errors_and_exits_on_cancel():
    task, _, _, _ = _task()
    task._tick = AsyncMock(side_effect=[None, RuntimeError("boom"), asyncio.CancelledError()])

    with patch("task.background.intraday.overseas_opening_reconcile_task.asyncio.sleep",
               new_callable=AsyncMock):
        await task._loop()

    task._logger.error.assert_called_once()
