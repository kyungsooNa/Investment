"""
E2E integration tests for restart restore + opening position reconcile flow.

Tests:
1. restore_state_from_broker: 미체결 주문이 있으면 SUBMITTED OrderContext로 복원되고
   이후 신규 주문 진입은 정상 허용된다.
2. local-only HOLD: reconcile_with_broker가 force_close 처리하고 VirtualTradeService에서
   해당 종목을 제거한다. 이후 KillSwitch는 주문을 허용한다(경고만).
3. broker-only / 수량 불일치: 자동 주문 발생 없이 경고 로그만 기록된다.

Background tasks는 직접 start() 하지 않고 reconcile_once() / restore_state_from_broker()
를 직접 호출해 결정성을 보장한다 (hang 방지).
"""
from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.types import (
    ErrorCode,
    Exchange,
    OrderContext,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from services.opening_position_reconcile_service import OpeningPositionReconcileService
from services.order_execution_service import OrderExecutionService
from services.virtual_trade_service import VirtualTradeService

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "kis"
UNFILLED_FIXTURE = json.loads((FIXTURE_DIR / "inquire_unfilled_orders_e2e.json").read_text(encoding="utf-8"))
BALANCE_FIXTURE = json.loads((FIXTURE_DIR / "inquire_balance_e2e.json").read_text(encoding="utf-8"))


# ─── helpers ───────────────────────────────────────────────────────────────

def _success(data=None, msg="정상") -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=msg, data=data)


def _error(msg="오류") -> ResCommonResponse:
    return ResCommonResponse(rt_cd="1", msg1=msg, data=None)


def _make_oes(broker: AsyncMock) -> OrderExecutionService:
    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 5, 10, 9, 1, 0)
    clock.is_market_operating_hours.return_value = True
    return OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logging.getLogger("test_restart"),
        market_clock=clock,
    )


def _make_vts(holds: list) -> VirtualTradeService:
    """MagicMock 리포지토리로 VirtualTradeService 생성."""
    repo = MagicMock()
    repo.get_holds.return_value = holds
    repo.log_sell_async = AsyncMock(return_value=None)
    vts = VirtualTradeService(repository=repo)
    return vts


# ─── Scenario 1: restore_state_from_broker ──────────────────────────────────

@pytest.mark.asyncio
async def test_restore_state_from_broker_registers_submitted_context():
    """재시작 후 미체결 1건이 SUBMITTED OrderContext로 복원된다."""
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)

    unfilled_data = {"output1": UNFILLED_FIXTURE["output1"]}
    broker.inquire_unfilled_orders = AsyncMock(return_value=_success(data=unfilled_data))
    # 당일 체결내역 없음
    broker.inquire_daily_ccld = AsyncMock(return_value=_success(data={"output1": []}))

    svc = _make_oes(broker)
    restored = await svc.restore_state_from_broker()

    assert restored >= 1
    # 복원된 컨텍스트가 SUBMITTED 상태로 등록됐는지 확인
    order_key = svc._make_order_key("005930", OrderSide.BUY, Exchange.KRX)
    assert order_key in svc._order_states
    ctx = svc._order_states[order_key]
    assert ctx.state == OrderState.SUBMITTED
    assert ctx.broker_order_no == "R001"
    assert ctx.stock_code == "005930"
    assert ctx.source == "restored"


@pytest.mark.asyncio
async def test_restore_state_then_orders_allowed():
    """복원 후 KillSwitch가 없으면 신규 주문이 차단되지 않는다."""
    broker = AsyncMock()
    broker.env = MagicMock(is_paper_trading=False)
    broker.inquire_unfilled_orders = AsyncMock(
        return_value=_success(data={"output1": UNFILLED_FIXTURE["output1"]})
    )
    broker.inquire_daily_ccld = AsyncMock(return_value=_success(data={"output1": []}))
    broker.place_stock_order = AsyncMock(return_value=_success(data={"KRX_FWDG_ORD_ORGNO": "", "ODNO": "NEW001", "ORD_TMD": "100000"}))

    clock = MagicMock()
    clock.get_current_kst_time.return_value = datetime(2026, 5, 10, 9, 1, 0)
    clock.is_market_operating_hours.return_value = True
    calendar = AsyncMock()
    calendar.is_market_open_now = AsyncMock(return_value=True)

    svc = OrderExecutionService(
        broker_api_wrapper=broker,
        logger=logging.getLogger("test_restart2"),
        market_clock=clock,
        market_calendar_service=calendar,
    )
    await svc.restore_state_from_broker()

    # 다른 종목 신규 매수 — 차단 없이 broker 호출로 이어져야 함
    result = await svc.handle_place_buy_order("000660", 120000, 1)
    broker.place_stock_order.assert_awaited_once()
    assert result.rt_cd == ErrorCode.SUCCESS.value


# ─── Scenario 2: local-only HOLD → force_close ──────────────────────────────

@pytest.mark.asyncio
async def test_local_only_hold_triggers_force_close():
    """로컬 HOLD이나 broker 잔고 없음 → force_close 처리, VTS에서 해당 종목 제거."""
    # broker 잔고에 005930 없음
    broker_holds = BALANCE_FIXTURE["scenarios"]["local_only"]["output1"]  # []
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": broker_holds})
    )

    # 로컬에는 005930 1주 보유
    vts = _make_vts([{"code": "005930", "strategy": "테스트", "qty": 1}])

    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=logging.getLogger("test_fc"),
    )
    result = await svc.reconcile_once()

    assert "005930" in result["force_closed"]
    assert result["unknown_in_broker"] == []
    vts._repo.log_sell_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_close_does_not_trip_kill_switch():
    """force_close 후에도 KillSwitch는 주문을 허용한다 (경고만)."""
    from services.kill_switch_service import KillSwitchService
    from config.config_loader import KillSwitchConfig

    broker_holds = BALANCE_FIXTURE["scenarios"]["local_only"]["output1"]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": broker_holds})
    )

    vts = _make_vts([{"code": "005930", "strategy": "테스트", "qty": 1}])

    cfg = KillSwitchConfig(
        enabled=True,
        daily_loss_threshold_won=5_000_000,
        max_consecutive_losses=5,
        max_consecutive_api_errors=5,
        state_file_path=str(Path(tempfile.gettempdir()) / f"kill_switch_test_{uuid.uuid4().hex}.json"),
    )
    ks = KillSwitchService(config=cfg, notification_service=AsyncMock(), logger=logging.getLogger("ks"))
    # kill_switch_state.json 저장을 방지
    ks._save_state = MagicMock()

    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=logging.getLogger("test_fc_ks"),
    )
    await svc.reconcile_once()

    allowed, reason = await ks.check_orders_allowed()
    assert allowed is True
    assert reason is None


# ─── Scenario 3: broker-only / 수량 불일치 → 경고만 ─────────────────────────

@pytest.mark.asyncio
async def test_broker_only_does_not_auto_insert():
    """broker에만 있는 종목은 자동 주문/insert 없이 경고만 기록된다."""
    broker_holds = BALANCE_FIXTURE["scenarios"]["broker_only"]["output1"]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": broker_holds})
    )

    # 로컬에는 000660 없음
    vts = _make_vts([])

    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=logging.getLogger("test_bo"),
    )
    result = await svc.reconcile_once()

    assert result["force_closed"] == []
    assert "000660" in result["unknown_in_broker"]
    vts._repo.log_sell_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_quantity_mismatch_does_not_auto_correct():
    """수량 불일치는 자동 정정 없이 경고만 기록된다."""
    broker_holds = BALANCE_FIXTURE["scenarios"]["quantity_mismatch"]["output1"]
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(
        return_value=_success(data={"output1": broker_holds})
    )

    # 로컬 보유 수량 != broker 수량
    vts = _make_vts([{"code": "035420", "strategy": "테스트", "qty": 7}])  # broker=3

    svc = OpeningPositionReconcileService(
        broker=broker,
        virtual_trade_service=vts,
        logger=logging.getLogger("test_qm"),
    )
    result = await svc.reconcile_once()

    assert result["force_closed"] == []
    mismatches = result["quantity_mismatches"]
    assert any(m["code"] == "035420" for m in mismatches)
    vts._repo.log_sell_async.assert_not_awaited()
