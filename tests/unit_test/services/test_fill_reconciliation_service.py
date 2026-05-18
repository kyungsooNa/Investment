from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    ErrorCode,
    Exchange,
    OrderContext,
    OrderExecutionReport,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from services.fill_reconciliation_service import FillReconciliationService
from services.order_state_machine import OrderStateMachine
from services.execution_quality_reporter import ExecutionQualityReporter


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()
        self.debug = MagicMock()
        self.exception = MagicMock()


@pytest.fixture
def fixed_now():
    return datetime(2026, 5, 17, 10, 0, 0)


@pytest.fixture
def fsm(fixed_now):
    return OrderStateMachine(logger=_Logger(), now_provider=lambda: fixed_now)


@pytest.fixture
def reporter():
    return ExecutionQualityReporter(logger=_Logger(), config=None, notification_service=None)


@pytest.fixture
def broker():
    mock = AsyncMock()
    mock.inquire_unfilled_orders = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output": []}))
    mock.inquire_filled_history = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output": []}))
    mock.get_account_balance = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output1": []}))
    mock.inquire_daily_ccld = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output": []}))
    mock.cancel_stock_order = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="취소 성공", data=None))
    return mock


def _make_service(*, broker, fsm, reporter, fixed_now, paper=False, **overrides):
    defaults = dict(
        broker_api_wrapper=broker,
        logger=_Logger(),
        state_machine=fsm,
        execution_quality_reporter=reporter,
        virtual_trade_service=None,
        kill_switch_service=None,
        account_snapshot_cache=None,
        market_clock=None,
        notification_service=None,
        now_provider=lambda: fixed_now,
        is_paper_trading_fn=lambda: paper,
    )
    defaults.update(overrides)
    return FillReconciliationService(**defaults)


def _make_context(**overrides) -> OrderContext:
    base = dict(
        order_key="KRX:005930:BUY",
        stock_code="005930",
        side=OrderSide.BUY,
        state=OrderState.PENDING_SUBMIT,
        exchange=Exchange.KRX,
        price=70000,
        qty=10,
    )
    base.update(overrides)
    return OrderContext(**base)


# ── is_reconcile_alarm_active ─────────────────────────────────────────────

def test_reconcile_alarm_starts_false(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    assert svc.is_reconcile_alarm_active() is False


# ── apply_execution_report: 기본 검증 + lock ────────────────────────────

@pytest.mark.asyncio
async def test_apply_execution_report_ignores_missing_order_no(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    report = OrderExecutionReport(broker_order_no="", stock_code="005930")
    result = await svc.apply_execution_report(report)
    assert result is None


@pytest.mark.asyncio
async def test_apply_execution_report_warns_when_no_matching_context(broker, fsm, reporter, fixed_now):
    logger = _Logger()
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    svc.logger = logger
    report = OrderExecutionReport(broker_order_no="UNKNOWN", stock_code="005930", side=OrderSide.BUY)
    result = await svc.apply_execution_report(report)
    assert result is None
    logger.warning.assert_called()


@pytest.mark.asyncio
async def test_apply_execution_report_filled_transitions_state(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    ctx = fsm.register(_make_context())
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.BUY,
        fill_qty=10, fill_price=70500, cumulative_filled_qty=10, remaining_qty=0,
    )
    after = await svc.apply_execution_report(report)
    assert after is not None
    assert after.state == OrderState.FILLED
    assert after.filled_qty == 10


# ── reconcile_orders_with_broker: paper mode skip ─────────────────────────

@pytest.mark.asyncio
async def test_reconcile_skips_in_paper_mode(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=True)
    result = await svc.reconcile_orders_with_broker()
    assert result == 0
    broker.inquire_unfilled_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_triggers_alarm_on_first_mismatch(broker, fsm, reporter, fixed_now):
    """broker 어디에도 없는 활성 주문 → 1회 mismatch + alarm True."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=False)
    fsm.register(_make_context(broker_order_no="O0001"))
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED, broker_order_no="O0001")

    broker.inquire_unfilled_orders.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": []})
    broker.inquire_filled_history.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": []})
    broker.get_account_balance.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output1": []})

    mismatch = await svc.reconcile_orders_with_broker()
    assert mismatch == 1
    assert svc.is_reconcile_alarm_active() is True


@pytest.mark.asyncio
async def test_reconcile_releases_alarm_when_no_mismatch(broker, fsm, reporter, fixed_now):
    """이전에 알람이 켜진 상태에서 mismatch 0건이면 자동 해제."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=False)
    svc._reconcile_alarm = True  # 기존 알람 상태 시뮬레이션

    mismatch = await svc.reconcile_orders_with_broker()
    assert mismatch == 0
    assert svc.is_reconcile_alarm_active() is False


@pytest.mark.asyncio
async def test_reconcile_two_consecutive_with_evidence_marks_canceled(broker, fsm, reporter, fixed_now):
    """2회 연속 mismatch + 잔고 0 + 체결 0 (BUY) → CANCELED 추정 전이."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=False)
    fsm.register(_make_context(broker_order_no="O0001"))
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED, broker_order_no="O0001")

    # 첫 번째 reconcile: mismatch 1
    await svc.reconcile_orders_with_broker()
    assert fsm.lookup("KRX:005930:BUY").state == OrderState.SUBMITTED

    # 두 번째 reconcile: 같은 mismatch → CANCELED 추정
    await svc.reconcile_orders_with_broker()
    assert fsm.lookup("KRX:005930:BUY").state == OrderState.CANCELED


# ── restore_state_from_broker ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_skips_in_paper_mode(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=True)
    result = await svc.restore_state_from_broker()
    assert result == 0
    broker.inquire_unfilled_orders.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_rebuilds_submitted_from_unfilled(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=False)
    broker.inquire_unfilled_orders.return_value = ResCommonResponse(
        rt_cd="0", msg1="",
        data={"output": [
            {"odno": "O100", "pdno": "005930", "sll_buy_dvsn_cd": "02",
             "ord_qty": "10", "ord_unpr": "70000", "tot_ccld_qty": "0"},
        ]}
    )

    count = await svc.restore_state_from_broker()
    assert count == 1
    restored = fsm.lookup("KRX:005930:BUY")
    assert restored is not None
    assert restored.state == OrderState.SUBMITTED
    assert restored.broker_order_no == "O100"


@pytest.mark.asyncio
async def test_restore_skips_existing_order_key(broker, fsm, reporter, fixed_now):
    """이미 _order_states 에 있는 키는 복원 건너뛴다."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, paper=False)
    fsm.register(_make_context(broker_order_no="O100"))

    broker.inquire_unfilled_orders.return_value = ResCommonResponse(
        rt_cd="0", msg1="",
        data={"output": [
            {"odno": "O100", "pdno": "005930", "sll_buy_dvsn_cd": "02",
             "ord_qty": "10", "ord_unpr": "70000", "tot_ccld_qty": "0"},
        ]}
    )
    count = await svc.restore_state_from_broker()
    assert count == 0  # 기존 컨텍스트 보존, 새로 복원하지 않음


# ── cancel_order ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_order_returns_invalid_when_no_context_found(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    result = await svc.cancel_order(stock_code="005930", is_buy=True)
    assert result.rt_cd == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_cancel_order_returns_invalid_for_terminal_context(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    fsm.register(_make_context(broker_order_no="O100"))
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED, broker_order_no="O100")
    fsm.transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)

    result = await svc.cancel_order(stock_code="005930", is_buy=True)
    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    assert "이미 종료된" in result.msg1


@pytest.mark.asyncio
async def test_cancel_order_calls_broker_with_remaining_qty(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    fsm.register(_make_context(broker_order_no="O100", remaining_qty=7))
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED, broker_order_no="O100")

    result = await svc.cancel_order(stock_code="005930", is_buy=True)
    assert result.rt_cd == ErrorCode.SUCCESS.value
    broker.cancel_stock_order.assert_awaited_once()
    kwargs = broker.cancel_stock_order.await_args.kwargs
    assert kwargs["broker_order_no"] == "O100"


# ── _persist_virtual_trade_for_terminal_report ───────────────────────────

@pytest.mark.asyncio
async def test_persist_skips_when_no_virtual_trade_service(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    ctx = fsm.register(_make_context())
    transitioned_submitted = fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    transitioned_filled = fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)
    report = OrderExecutionReport(broker_order_no="X", stock_code="005930", side=OrderSide.BUY, fill_price=70000)
    result = await svc._persist_virtual_trade_for_terminal_report(transitioned_filled, report)
    assert result is transitioned_filled


@pytest.mark.asyncio
async def test_persist_records_buy_via_virtual_trade(broker, fsm, reporter, fixed_now):
    vts = AsyncMock()
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, virtual_trade_service=vts)
    ctx = fsm.register(_make_context(source="strategy:momentum"))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    filled = fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)

    report = OrderExecutionReport(broker_order_no="X", stock_code="005930", side=OrderSide.BUY, fill_price=70500)
    result = await svc._persist_virtual_trade_for_terminal_report(filled, report)

    vts.log_buy_async.assert_awaited_once()
    assert result.virtual_recorded_qty == 10


# ── resolve_submitted_order + mark_* ─────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_submitted_transitions_to_filled(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    fsm.register(_make_context())
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED)

    result = await svc.resolve_submitted_order("005930", True)
    assert result.state == OrderState.FILLED


@pytest.mark.asyncio
async def test_mark_order_partial_filled_transitions_state(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    fsm.register(_make_context())
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED)

    result = await svc.mark_order_partial_filled("005930", True, 4)
    assert result.state == OrderState.PARTIAL_FILLED
    assert result.filled_qty == 4


# ── poll_active_orders_once ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_returns_zero_when_no_active_contexts(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    applied = await svc.poll_active_orders_once()
    assert applied == 0


# ── check_stuck_orders_once: 기본 동작 ────────────────────────────────────

@pytest.mark.asyncio
async def test_check_stuck_orders_emits_warning_after_threshold(broker, fsm, reporter, fixed_now):
    notif = AsyncMock()
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now,
                        notification_service=notif, paper=True)  # paper → WARNING only
    entered = fixed_now - timedelta(seconds=90)
    ctx = _make_context(
        broker_order_no="O1",
        state=OrderState.SUBMITTED,
        state_entered_at=entered,
        created_at=entered,
    )
    fsm._order_states[ctx.order_key] = ctx
    fsm._order_no_index["O1"] = ctx.order_key

    count = await svc.check_stuck_orders_once(fixed_now)
    assert count == 1
    notif.emit.assert_awaited_once()
