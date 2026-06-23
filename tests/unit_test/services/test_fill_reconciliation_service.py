import asyncio
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
from services.notification_service import NotificationCategory, NotificationLevel
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


@pytest.mark.asyncio
async def test_apply_execution_report_filled_emits_completion_notification(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
    )
    ctx = fsm.register(_make_context(source="manual:수동매매"))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.BUY,
        fill_qty=10, fill_price=70500, cumulative_filled_qty=10, remaining_qty=0,
    )
    await svc.apply_execution_report(report)

    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    assert args[0] == NotificationCategory.TRADE
    assert args[1] == NotificationLevel.INFO
    assert args[2] == "매수 체결 완료"
    assert "체결 완료" in args[3]
    assert kwargs["metadata"]["state"] == OrderState.FILLED.value
    assert kwargs["metadata"]["filled_qty"] == 10


@pytest.mark.asyncio
async def test_apply_execution_report_filled_emits_strategy_final_notification(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
    )
    ctx = fsm.register(_make_context(
        order_key="KRX:005930:SELL",
        side=OrderSide.SELL,
        source="strategy:LarryWilliamsCB",
        strategy_notification={
            "strategy_name": "LarryWilliamsCB",
            "stock_name": "롯데쇼핑",
            "code": "005930",
            "action": "SELL",
            "price": 179400,
            "qty": 10,
            "buy_price": 170000,
            "reason": "칼손절",
        },
    ))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.SELL,
        fill_qty=10, fill_price=179300, cumulative_filled_qty=10, remaining_qty=0,
    )
    await svc.apply_execution_report(report)

    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    assert args[0] == NotificationCategory.STRATEGY
    assert args[1] == NotificationLevel.CRITICAL
    assert args[2] == "[LarryWilliamsCB] 롯데쇼핑 매도 체결 완료"
    assert "체결 완료" in args[3]
    assert "칼손절" in args[3]
    assert kwargs["metadata"]["strategy_name"] == "LarryWilliamsCB"
    assert kwargs["metadata"]["return_rate"] == pytest.approx(5.47)
    assert kwargs["metadata"]["state"] == OrderState.FILLED.value


@pytest.mark.asyncio
async def test_strategy_final_notification_labels_average_fill_price_and_total_amount(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
    )
    ctx = fsm.register(_make_context(
        order_key="KRX:252990:BUY",
        stock_code="252990",
        side=OrderSide.BUY,
        source="strategy:RSI2눌림목",
        price=12940,
        qty=154,
        strategy_notification={
            "strategy_name": "RSI2눌림목",
            "stock_name": "샘씨엔에스",
            "code": "252990",
            "action": "BUY",
            "price": 12940,
            "qty": 154,
            "reason": "RSI(2)=6.57 ≤ 10.0, Stage 2, 정상비중 진입",
        },
    ))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    await svc.apply_execution_report(OrderExecutionReport(
        broker_order_no="B0001", stock_code="252990", side=OrderSide.BUY,
        fill_qty=129, fill_price=12930, cumulative_filled_qty=129, remaining_qty=25,
    ))
    await svc.apply_execution_report(OrderExecutionReport(
        broker_order_no="B0001", stock_code="252990", side=OrderSide.BUY,
        fill_qty=25, fill_price=12940, cumulative_filled_qty=154, remaining_qty=0,
    ))

    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    message = args[3]
    assert "평균체결가: 12,931.62원 × 154/154주" in message
    assert "총체결금액: 1,991,470원" in message
    assert "체결: 12931.623376623376원" not in message
    assert kwargs["metadata"]["fill_price"] == pytest.approx(12931.623376623376)


@pytest.mark.asyncio
async def test_apply_execution_report_rejected_emits_failure_notification(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
    )
    ctx = fsm.register(_make_context())
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.REJECTED,
        message="증거금 부족",
    )
    await svc.apply_execution_report(report)

    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    assert args[0] == NotificationCategory.TRADE
    assert args[1] == NotificationLevel.ERROR
    assert args[2] == "매수 주문 실패"
    assert "증거금 부족" in args[3]
    assert kwargs["metadata"]["state"] == OrderState.REJECTED.value


@pytest.mark.asyncio
async def test_apply_execution_report_submitted_does_not_emit_completion_notification(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
    )
    ctx = fsm.register(_make_context())
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001",
        stock_code="005930",
        side=OrderSide.BUY,
        event_state=OrderState.SUBMITTED,
        fill_qty=0,
    )
    await svc.apply_execution_report(report)

    notification.emit.assert_not_awaited()


# ── order_to_fill_latency event (P2-2 L354 잔여 후속) ─────────────────────


def _extract_logger_events(logger_mock, event_name: str) -> list[dict]:
    """logger.info call_args 중 dict 인자에서 매칭되는 event 만 추린다."""
    matches: list[dict] = []
    for call in logger_mock.info.call_args_list:
        if not call.args:
            continue
        arg = call.args[0]
        if isinstance(arg, dict) and arg.get("event") == event_name:
            matches.append(arg)
    return matches


@pytest.mark.asyncio
async def test_apply_execution_report_emits_order_to_fill_latency_event_on_filled(
    broker, fsm, reporter, fixed_now
):
    """FILLED 전이 시 order_to_fill_latency log event 발행."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    # created_at 을 5초 전으로 미리 stamp 해 측정 가능한 latency 생성
    ctx = fsm.register(_make_context(created_at=fixed_now - timedelta(seconds=5)))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.BUY,
        fill_qty=10, fill_price=70500, cumulative_filled_qty=10, remaining_qty=0,
    )
    after = await svc.apply_execution_report(report)

    assert after is not None
    assert after.state == OrderState.FILLED

    events = _extract_logger_events(svc.logger, "order_to_fill_latency")
    assert len(events) == 1
    payload = events[0]
    assert payload["order_key"] == ctx.order_key
    assert payload["code"] == "005930"
    assert payload["side"] == "BUY"
    assert payload["latency_ms"] == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_apply_execution_report_does_not_emit_latency_for_partial_fill(
    broker, fsm, reporter, fixed_now
):
    """PARTIAL_FILLED 전이 시 order_to_fill_latency 미발행."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    ctx = fsm.register(_make_context(created_at=fixed_now - timedelta(seconds=5)))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.BUY,
        fill_qty=4, fill_price=70500, cumulative_filled_qty=4, remaining_qty=6,
    )
    after = await svc.apply_execution_report(report)

    assert after is not None
    assert after.state == OrderState.PARTIAL_FILLED

    events = _extract_logger_events(svc.logger, "order_to_fill_latency")
    assert events == []


@pytest.mark.asyncio
async def test_apply_execution_report_does_not_emit_latency_twice_on_already_filled(
    broker, fsm, reporter, fixed_now
):
    """이미 FILLED 인 주문에 대한 후속 webhook 에서는 중복 emission 없음."""
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    ctx = fsm.register(_make_context(created_at=fixed_now - timedelta(seconds=5)))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED, broker_order_no="B0001")
    # 사전에 FILLED 로 전이된 상태를 가정
    fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)
    svc.logger.info.reset_mock()  # 사전 전이로 인한 잡음 제거

    report = OrderExecutionReport(
        broker_order_no="B0001", stock_code="005930", side=OrderSide.BUY,
        fill_qty=10, fill_price=70500, cumulative_filled_qty=10, remaining_qty=0,
    )
    await svc.apply_execution_report(report)

    events = _extract_logger_events(svc.logger, "order_to_fill_latency")
    assert events == []


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
async def test_consume_force_reconcile_logs_and_continues_full_scan(broker, fsm, reporter, fixed_now):
    logger = _Logger()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        paper=False,
        logger=logger,
    )
    fsm._force_reconcile_requested = True

    mismatch = await svc.reconcile_orders_with_broker()

    assert mismatch == 0
    broker.inquire_unfilled_orders.assert_awaited_once()
    assert fsm.consume_force_reconcile_request() is False
    assert any("safe_transition" in str(call) for call in logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_on_safe_transition_critical_sets_alarm_and_emits(broker, fsm, reporter, fixed_now):
    notification = AsyncMock()
    logger = _Logger()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
        logger=logger,
    )
    context = fsm.register(_make_context(broker_order_no="O0001"))

    svc.on_safe_transition_critical(context.order_key, context)
    await asyncio.gather(*list(svc._notification_tasks))

    assert svc.is_reconcile_alarm_active() is True
    assert svc._critical_alarm_manual_required is True
    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    assert args[0] == NotificationCategory.TRADE
    assert args[1] == NotificationLevel.CRITICAL
    assert args[2] == "Order Block: Safe Transition Mismatch"
    assert kwargs["metadata"]["order_key"] == context.order_key
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_on_broker_order_no_missing_sets_alarm_and_emits_notification(
    broker, fsm, reporter, fixed_now
):
    notification = AsyncMock()
    logger = _Logger()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=notification,
        logger=logger,
    )
    raw_payload = {"KRX_FWDG_ORD_ORGNO": "00950", "unexpected_key": "VAL1"}
    result = ResCommonResponse(rt_cd="0", msg1="정상처리 되었습니다.", data=raw_payload)

    svc.on_broker_order_no_missing(result, "005930", "KRX:005930:BUY")
    await asyncio.gather(*list(svc._notification_tasks))

    assert svc.is_reconcile_alarm_active() is True
    assert svc._critical_alarm_manual_required is True
    notification.emit.assert_awaited_once()
    args, kwargs = notification.emit.await_args
    assert args[0] == NotificationCategory.TRADE
    assert args[1] == NotificationLevel.CRITICAL
    metadata = kwargs["metadata"]
    assert metadata["alert_type"] == "broker_order_no_missing"
    assert metadata["stock_code"] == "005930"
    assert metadata["order_key"] == "KRX:005930:BUY"
    assert metadata["rt_cd"] == "0"
    assert metadata["msg1"] == "정상처리 되었습니다."
    assert "KRX_FWDG_ORD_ORGNO" in metadata["raw_data"]
    logger.error.assert_called()


def test_on_broker_order_no_missing_without_notification_service_only_sets_alarm(
    broker, fsm, reporter, fixed_now
):
    logger = _Logger()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        notification_service=None,
        logger=logger,
    )
    result = ResCommonResponse(rt_cd="0", msg1="OK", data={"foo": "bar"})

    svc.on_broker_order_no_missing(result, "005930", "KRX:005930:BUY")

    assert svc.is_reconcile_alarm_active() is True
    assert svc._critical_alarm_manual_required is True
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_critical_alarm_skips_auto_reset(broker, fsm, reporter, fixed_now):
    logger = _Logger()
    svc = _make_service(
        broker=broker,
        fsm=fsm,
        reporter=reporter,
        fixed_now=fixed_now,
        paper=False,
        logger=logger,
    )
    svc._reconcile_alarm = True
    svc._critical_alarm_manual_required = True

    mismatch = await svc.reconcile_orders_with_broker()

    assert mismatch == 0
    assert svc.is_reconcile_alarm_active() is True
    assert any("auto-reset 보류" in str(call) for call in logger.info.call_args_list)


def test_reset_reconcile_alarm_clears_critical(broker, fsm, reporter, fixed_now):
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now)
    svc._reconcile_alarm = True
    svc._critical_alarm_manual_required = True

    svc.reset_reconcile_alarm()

    assert svc.is_reconcile_alarm_active() is False
    assert svc._critical_alarm_manual_required is False


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


@pytest.mark.asyncio
async def test_persist_records_buy_signal_metadata_via_virtual_trade(broker, fsm, reporter, fixed_now):
    vts = AsyncMock()
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, virtual_trade_service=vts)
    ctx = fsm.register(_make_context(
        source="strategy:momentum",
        volatility_20d_annualized=0.42,
        config_hash="abc123def456",
        invalidation_price=68000,
        stop_loss_price=66000,
        target_price=78000,
        entry_reason="pocket_pivot_breakout",
        trailing_rule="ma20_after_profit",
        expected_holding_period_days=20,
        confidence=0.75,
        required_data=["daily_ohlcv", "execution_strength"],
        market_regime={"kospi": "bull", "kosdaq": "sideways", "stock_market": "KOSPI"},
    ))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    filled = fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)

    report = OrderExecutionReport(broker_order_no="X", stock_code="005930", side=OrderSide.BUY, fill_price=70500)
    await svc._persist_virtual_trade_for_terminal_report(filled, report)

    vts.log_buy_async.assert_awaited_once_with(
        "momentum",
        "005930",
        70500,
        10,
        volatility_20d_annualized=0.42,
        config_hash="abc123def456",
        invalidation_price=68000,
        stop_loss_price=66000,
        target_price=78000,
        entry_reason="pocket_pivot_breakout",
        trailing_rule="ma20_after_profit",
        expected_holding_period_days=20,
        confidence=0.75,
        required_data=["daily_ohlcv", "execution_strength"],
        market_regime={"kospi": "bull", "kosdaq": "sideways", "stock_market": "KOSPI"},
    )


# ── resolve_submitted_order + mark_* ─────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_records_average_fill_price_when_available(broker, fsm, reporter, fixed_now):
    vts = AsyncMock()
    svc = _make_service(broker=broker, fsm=fsm, reporter=reporter, fixed_now=fixed_now, virtual_trade_service=vts)
    ctx = fsm.register(_make_context(source="strategy:momentum"))
    fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    filled = fsm.transition(
        ctx.order_key,
        OrderState.FILLED,
        filled_qty=10,
        average_fill_price=70525.5,
    )

    report = OrderExecutionReport(broker_order_no="X", stock_code="005930", side=OrderSide.BUY, fill_price=70600)
    await svc._persist_virtual_trade_for_terminal_report(filled, report)

    args = vts.log_buy_async.await_args.args
    assert args[2] == 70525.5


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
