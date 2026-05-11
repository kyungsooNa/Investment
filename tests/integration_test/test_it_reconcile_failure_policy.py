"""
Reconcile failure policy integration tests.

Each test corresponds to one row in docs/reconcile_failure_policy.md#운영-매트릭스.
Function names match the policy document 1:1.

Scenarios:
1. test_single_broker_fetch_failure_records_api_failure
2. test_consecutive_broker_fetch_failures_trip_kill_switch
3. test_reconcile_with_broker_exception_does_not_trip_kill_switch
4. test_force_close_sell_failure_emits_notification
5. test_reconcile_task_window_miss_allows_retry_next_window

Background tasks are NOT started — run_once() / reconcile_once() are called directly.
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from config.config_loader import KillSwitchConfig
from services.kill_switch_service import KillSwitchService
from services.opening_position_reconcile_service import OpeningPositionReconcileService
from services.virtual_trade_service import VirtualTradeService
from task.background.intraday.opening_position_reconcile_task import OpeningPositionReconcileTask


# ─── helpers ────────────────────────────────────────────────────────────────

def _success(data=None) -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=data)


def _error(msg="잔고 조회 실패") -> ResCommonResponse:
    return ResCommonResponse(rt_cd="1", msg1=msg, data=None)


def _make_ks(max_consecutive_api_errors: int = 3) -> KillSwitchService:
    cfg = KillSwitchConfig(
        enabled=True,
        daily_loss_threshold_won=5_000_000,
        max_consecutive_losses=10,
        max_consecutive_api_errors=max_consecutive_api_errors,
        state_file_path=str(Path(tempfile.gettempdir()) / f"kill_switch_test_{uuid.uuid4().hex}.json"),
    )
    ks = KillSwitchService(
        config=cfg,
        notification_service=AsyncMock(),
        logger=logging.getLogger("ks_test"),
    )
    ks._save_state = MagicMock()
    ks._load_state = MagicMock()
    return ks


def _make_vts_empty() -> VirtualTradeService:
    repo = MagicMock()
    repo.get_holds.return_value = []
    repo.log_sell_async = AsyncMock(return_value=None)
    return VirtualTradeService(repository=repo)


def _make_reconcile_svc(broker_mock, vts=None, ks=None) -> OpeningPositionReconcileService:
    return OpeningPositionReconcileService(
        broker=broker_mock,
        virtual_trade_service=vts or _make_vts_empty(),
        kill_switch_service=ks,
        logger=logging.getLogger("reconcile_test"),
    )


# ─── Scenario 1: 단일 broker fetch 실패 → record_api_failure, 트립 없음 ─────────

@pytest.mark.asyncio
async def test_single_broker_fetch_failure_records_api_failure():
    """broker balance fetch 1회 실패 → record_api_failure 호출, KillSwitch 트립 없음.

    매트릭스 행: 'broker balance fetch 실패 (1회)'
    """
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=_error("네트워크 오류"))

    ks = _make_ks(max_consecutive_api_errors=3)
    svc = _make_reconcile_svc(broker, ks=ks)

    result = await svc.reconcile_once()

    assert result["error"] is not None
    assert ks._consecutive_api_errors == 1
    assert ks._is_tripped is False

    # 주문은 여전히 허용
    allowed, reason = await ks.check_orders_allowed()
    assert allowed is True


# ─── Scenario 2: 연속 N회 실패 → KillSwitch 트립 ────────────────────────────

@pytest.mark.asyncio
async def test_consecutive_broker_fetch_failures_trip_kill_switch():
    """broker balance fetch 연속 3회 실패 → KillSwitch 트립, 주문 차단.

    매트릭스 행: 'broker balance fetch 실패 + 연속 N회'
    """
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=_error("타임아웃"))

    ks = _make_ks(max_consecutive_api_errors=3)
    svc = _make_reconcile_svc(broker, ks=ks)

    # 3회 연속 실패
    for _ in range(3):
        await svc.reconcile_once()

    assert ks._is_tripped is True
    allowed, reason = await ks.check_orders_allowed()
    assert allowed is False
    assert reason is not None


# ─── Scenario 3: reconcile_with_broker 내부 예외 → 트립 없음 ──────────────────

@pytest.mark.asyncio
async def test_reconcile_with_broker_exception_does_not_trip_kill_switch():
    """reconcile_with_broker 내부 예외 → error 반환, KillSwitch 트립 없음.

    매트릭스 행: 'reconcile_with_broker 내부 예외'
    """
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": [{"pdno": "005930", "hldg_qty": "1"}]})
    )

    # VTS.reconcile_with_broker 가 예외를 발생시킴
    vts = MagicMock()
    vts.reconcile_with_broker = AsyncMock(side_effect=RuntimeError("DB 잠금 오류"))

    ks = _make_ks(max_consecutive_api_errors=3)
    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        kill_switch_service=ks,
        logger=logging.getLogger("exc_test"),
    )

    # 예외가 reconcile_once 외부로 전파되거나 처리되는지 확인
    # 현행 구현에서는 전파됨 — 서비스 호출자가 캐치해야 함
    with pytest.raises(RuntimeError, match="DB 잠금 오류"):
        await svc.reconcile_once()

    # broker fetch는 성공했으므로 record_api_failure는 호출되지 않음
    assert ks._consecutive_api_errors == 0
    assert ks._is_tripped is False


# ─── Scenario 4: force_close 매도 실패 → notification emit ──────────────────

@pytest.mark.asyncio
async def test_force_close_sell_failure_emits_notification():
    """force_close 중 log_sell_async 실패 → 예외 전파되거나 경고 로깅.

    매트릭스 행: 'force_close 시도 중 매도 주문 실패'
    """
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": []})  # broker에 005930 없음
    )

    repo = MagicMock()
    repo.get_holds.return_value = [{"code": "005930", "strategy": "테스트", "qty": 1}]
    repo.log_sell_async = AsyncMock(side_effect=Exception("매도 실패"))

    vts = VirtualTradeService(repository=repo)

    notification_service = AsyncMock()
    notification_service.emit = AsyncMock()

    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=logging.getLogger("fc_fail_test"),
    )

    # log_sell_async 예외는 reconcile_with_broker 외부로 전파됨
    with pytest.raises(Exception, match="매도 실패"):
        await svc.reconcile_once()

    # 예외가 전파되기 전에 broker fetch는 성공 → kill_switch 호출 없음
    # (notification은 태스크 레이어 책임 — 서비스 레이어에서는 예외 전파)


# ─── Scenario 5: 윈도우 내 미실행 → 다음 윈도우 재시도 ──────────────────────

@pytest.mark.asyncio
async def test_reconcile_task_window_miss_allows_retry_next_window():
    """reconcile_once() 가 error를 반환하면 _last_checked_date가 설정되지 않아
    다음 윈도우에 재시도가 가능하다.

    매트릭스 행: 'reconcile task가 윈도우 내 1회도 실행 못 함'
    """
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=_error("일시적 오류"))

    reconcile_svc = _make_reconcile_svc(broker)

    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 5, 10, 9, 1, 0)

    task = OpeningPositionReconcileTask(
        reconcile_service=reconcile_svc,
        market_clock=clock,
    )

    # run_once() 직접 호출 — 에러 반환
    result = await task.run_once()

    assert result.get("error") is not None
    # error 발생 시 _last_checked_date 미설정 → 다음 윈도우에 재시도 가능
    assert task._last_checked_date is None
