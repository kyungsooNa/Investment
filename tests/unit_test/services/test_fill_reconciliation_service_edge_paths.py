"""FillReconciliationService 의 알림 조립·조회 방어 경로 테스트.

기존 테스트가 정상 체결/reconcile 흐름을 다루므로, 여기서는 이벤트 루프가 없는
동기 호출, 숫자 파싱 실패 fallback("N/A"), 취소·거부 알림 문구, 브로커 조회
응답이 깨졌을 때의 skip 처럼 실운영에서 어긋났을 때만 타는 분기를 채운다.
"""
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
from services.execution_quality_reporter import ExecutionQualityReporter
from services.notification_service import NotificationCategory, NotificationLevel
from services.order_state_machine import OrderStateMachine

FIXED_NOW = datetime(2026, 5, 17, 10, 0, 0)


def _logger():
    return MagicMock()


@pytest.fixture
def fsm():
    return OrderStateMachine(logger=_logger(), now_provider=lambda: FIXED_NOW)


@pytest.fixture
def broker():
    mock = AsyncMock()
    ok = ResCommonResponse(rt_cd="0", msg1="", data={"output": []})
    mock.inquire_unfilled_orders = AsyncMock(return_value=ok)
    mock.inquire_filled_history = AsyncMock(return_value=ok)
    mock.get_account_balance = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output1": []})
    )
    return mock


@pytest.fixture
def service(broker, fsm):
    return FillReconciliationService(
        broker_api_wrapper=broker,
        logger=_logger(),
        state_machine=fsm,
        execution_quality_reporter=ExecutionQualityReporter(
            logger=_logger(), config=None, notification_service=None
        ),
        now_provider=lambda: FIXED_NOW,
        is_paper_trading_fn=lambda: False,
    )


def _context(**overrides) -> OrderContext:
    base = dict(order_key="KRX:005930:BUY", stock_code="005930", side=OrderSide.BUY,
                state=OrderState.PENDING_SUBMIT, exchange=Exchange.KRX,
                price=70000, qty=10)
    base.update(overrides)
    return OrderContext(**base)


# --- source 분류 -------------------------------------------------------------

@pytest.mark.parametrize(
    "source, expected",
    [
        ("strategy:모멘텀", ("모멘텀", True)),
        ("strategy:", ("default", True)),
        ("strategy_force_exit:모멘텀", ("모멘텀", True)),
        ("strategy_force_exit:", ("default", True)),
        ("manual:내주문", ("내주문", False)),
        ("manual:", ("수동매매", False)),
        ("", ("수동매매", False)),
        ("web", ("수동매매", False)),
        ("reconcile:개장대사", ("reconcile:개장대사", False)),
    ],
)
def test_strategy_name_is_derived_from_the_order_source(source, expected):
    assert FillReconciliationService._strategy_name_from_source(source) == expected


@pytest.mark.parametrize("source, expected",
                         [("reconcile:개장대사", True), ("manual:", False), (None, False)])
def test_reconcile_source_detection(source, expected):
    assert FillReconciliationService._is_reconcile_source(source) is expected


# --- 숫자 포매팅 -------------------------------------------------------------

@pytest.mark.parametrize(
    "buy, sell, expected",
    [("70000", "77000", 10.0), (None, 77000, None), ("숫자아님", 1, None), (0, 77000, None)],
)
def test_return_rate_calculation(buy, sell, expected):
    assert FillReconciliationService._calculate_return_rate(buy, sell) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, "N/A"), ("숫자아님", "N/A"), (0, "N/A"), (-1, "N/A"),
     (70000, "70,000원"), (70000.5, "70,000.50원")],
)
def test_won_formatting(raw, expected):
    assert FillReconciliationService._format_won(raw) == expected


def test_fill_total_is_derived_from_price_and_quantity_when_absent():
    context = _context(filled_qty=3)

    assert FillReconciliationService._format_fill_total_won(context, 70000) == "210,000원"


@pytest.mark.parametrize(
    "state, requested, expected",
    [
        (OrderState.FILLED, 20, 20),
        (OrderState.FILLED, "숫자아님", 10),
        (OrderState.CANCELED, 20, 20),
        (OrderState.CANCELED, None, 10),
    ],
)
def test_displayed_order_quantity_takes_the_largest_known_value(state, requested, expected):
    context = _context(state=state, filled_qty=5)

    assert FillReconciliationService._display_order_qty(context, requested) == expected


# --- 이벤트 루프 없는 알림 예약 -------------------------------------------------

def test_safe_transition_alert_is_skipped_without_a_notification_service(service):
    service.on_safe_transition_critical("KRX:005930:BUY", _context())

    service.logger.error.assert_called_once()


def test_safe_transition_alert_is_skipped_outside_a_running_loop(service):
    service._notification_service = MagicMock(emit=AsyncMock())

    service.on_safe_transition_critical("KRX:005930:BUY", _context())

    assert any("running event loop 없음" in str(c)
               for c in service.logger.warning.call_args_list)


def test_broker_order_no_alert_is_skipped_without_a_notification_service(service):
    service.on_broker_order_no_missing(
        ResCommonResponse(rt_cd="0", msg1="", data=None), "005930", "KRX:005930:BUY"
    )

    service.logger.error.assert_called_once()


def test_broker_order_no_alert_is_skipped_outside_a_running_loop(service):
    service._notification_service = MagicMock(emit=AsyncMock())

    service.on_broker_order_no_missing(
        ResCommonResponse(rt_cd="0", msg1="", data=None), "005930", "KRX:005930:BUY"
    )

    assert any("running event loop 없음" in str(c)
               for c in service.logger.warning.call_args_list)


# --- terminal 알림 -----------------------------------------------------------

def _notifying(service):
    service._notification_service = MagicMock(emit=AsyncMock())
    return service._notification_service


@pytest.mark.asyncio
async def test_non_terminal_state_emits_no_notification(service):
    notifications = _notifying(service)

    await service._emit_terminal_order_notification(_context(state=OrderState.SUBMITTED), None)

    notifications.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_same_terminal_state_is_only_notified_once(service):
    notifications = _notifying(service)
    context = _context(state=OrderState.FILLED, filled_qty=10, average_fill_price=70000)

    await service._emit_terminal_order_notification(context, None)
    await service._emit_terminal_order_notification(context, None)

    assert notifications.emit.await_count == 1


@pytest.mark.asyncio
async def test_rejected_order_reports_the_broker_reason(service):
    notifications = _notifying(service)
    context = _context(state=OrderState.REJECTED, last_error_message="증거금 부족")

    await service._emit_terminal_order_notification(context, None)

    args = notifications.emit.await_args.args
    assert args[1] == NotificationLevel.ERROR
    assert "증거금 부족" in args[3]


@pytest.mark.asyncio
async def test_canceled_order_without_fills_reports_an_unfilled_cancel(service):
    notifications = _notifying(service)
    context = _context(state=OrderState.CANCELED, last_error_message="사용자 취소")

    await service._emit_terminal_order_notification(context, None)

    assert "미체결 취소" in notifications.emit.await_args.args[3]


@pytest.mark.asyncio
async def test_canceled_order_with_fills_reports_the_partial_fill(service):
    notifications = _notifying(service)
    context = _context(state=OrderState.CANCELED, filled_qty=4)

    await service._emit_terminal_order_notification(context, None)

    assert "부분체결 후 취소" in notifications.emit.await_args.args[3]
    assert "체결 4/10주" in notifications.emit.await_args.args[3]


@pytest.mark.asyncio
async def test_canceled_order_falls_back_to_the_report_message(service):
    notifications = _notifying(service)
    report = OrderExecutionReport(
        broker_order_no="1", stock_code="005930", side=OrderSide.BUY,
        message="브로커 취소 통보",
    )

    await service._emit_terminal_order_notification(
        _context(state=OrderState.CANCELED), report
    )

    assert "브로커 취소 통보" in notifications.emit.await_args.args[3]


@pytest.mark.asyncio
async def test_strategy_notification_renders_the_full_fill_summary(service):
    notifications = _notifying(service)
    context = _context(
        state=OrderState.FILLED, filled_qty=10, average_fill_price=71000,
        source="strategy:모멘텀",
        strategy_notification={"stock_name": "삼성전자", "buy_price": 70000,
                               "price": 70000, "qty": 10},
    )

    await service._emit_terminal_order_notification(context, None)

    args = notifications.emit.await_args.args
    assert args[0] == NotificationCategory.STRATEGY
    assert args[1] == NotificationLevel.CRITICAL
    assert "[모멘텀] 삼성전자 매수 체결 완료" == args[2]
    assert "평균체결가: 71,000원" in args[3]
    assert "총체결금액: 710,000원" in args[3]


@pytest.mark.asyncio
async def test_strategy_notification_marks_a_failed_order_and_its_reason(service):
    notifications = _notifying(service)
    context = _context(
        state=OrderState.REJECTED, source="strategy:모멘텀",
        last_error_message="증거금 부족",
        strategy_notification={"stock_name": "삼성전자", "price": "숫자아님", "qty": 10},
    )

    await service._emit_terminal_order_notification(context, None)

    args = notifications.emit.await_args.args
    assert args[1] == NotificationLevel.ERROR
    assert "실패" in args[2]
    assert "주문: N/A × 10주" in args[3]
    assert "실패: 증거금 부족" in args[3]


@pytest.mark.asyncio
async def test_notification_failure_does_not_mark_the_state_as_notified(service):
    notifications = _notifying(service)
    notifications.emit = AsyncMock(side_effect=RuntimeError("텔레그램 오류"))
    context = _context(state=OrderState.FILLED, filled_qty=10)

    await service._emit_terminal_order_notification(context, None)

    assert service._terminal_notification_sent == set()
    service.logger.warning.assert_called()


# --- 가상매매 기록 스킵 조건 ---------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_kwargs",
    [
        {"state": OrderState.SUBMITTED},                       # terminal 아님
        {"state": OrderState.REJECTED},                        # 거부
        {"state": OrderState.FILLED, "filled_qty": 0},         # 체결 0
        {"state": OrderState.FILLED, "filled_qty": 5,
         "virtual_recorded_qty": 5},                            # 이미 기록됨
    ],
)
async def test_virtual_trade_recording_is_skipped_for_non_recordable_states(
    service, context_kwargs
):
    virtual = MagicMock()
    service._virtual_trade_service = virtual
    context = _context(**context_kwargs)
    report = OrderExecutionReport(broker_order_no="1", stock_code="005930",
                                  side=OrderSide.BUY)

    await service._persist_virtual_trade_for_terminal_report(context, report)

    virtual.log_buy.assert_not_called()


# --- 지연 주문 경보 등급 -------------------------------------------------------

@pytest.mark.parametrize(
    "state, age, paper, expected",
    [
        (OrderState.FILLED, 1000, False, None),          # 활성 상태가 아니면 경보 없음
        (OrderState.SUBMITTED, 10, False, None),         # 경고 임계 미만
        (OrderState.SUBMITTED, 100, False, NotificationLevel.WARNING),
        (OrderState.SUBMITTED, 1000, False, NotificationLevel.CRITICAL),
        (OrderState.PARTIAL_FILLED, 1000, True, NotificationLevel.WARNING),  # 모의는 경고까지
    ],
)
def test_stuck_order_alert_level(service, state, age, paper, expected):
    service._is_paper_trading = lambda: paper

    assert service._get_stuck_order_alert_level(_context(state=state), age) is expected


@pytest.mark.asyncio
async def test_contexts_without_a_timestamp_are_skipped_by_the_stuck_check(service, fsm):
    context = fsm.register(_context(state=OrderState.SUBMITTED))
    context.state_entered_at = None
    context.created_at = None

    assert await service.check_stuck_orders_once() == 0


# --- 개장 대사 조회 방어 -------------------------------------------------------

@pytest.mark.asyncio
async def test_unfilled_rows_without_ids_or_numbers_are_skipped(service, broker, fsm):
    broker.inquire_unfilled_orders = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="", data={"output": [
            {"odno": "", "pdno": "005930"},
            {"odno": "1", "pdno": ""},
            {"odno": "2", "pdno": "005930", "ord_qty": "숫자아님"},
        ]},
    ))

    await service.restore_state_from_broker()

    assert fsm.active_contexts() == []


@pytest.mark.asyncio
async def test_filled_rows_without_ids_or_numbers_are_skipped(service, broker, fsm):
    broker.inquire_filled_history = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="", data={"output": [
            {"odno": "", "pdno": "005930"},
            {"odno": "1", "pdno": ""},
            {"odno": "2", "pdno": "005930", "ord_qty": "숫자아님"},
        ]},
    ))

    await service.restore_state_from_broker()

    assert fsm.lookup(fsm.make_order_key("005930", OrderSide.SELL, Exchange.KRX)) is None


@pytest.mark.asyncio
async def test_reconcile_stops_when_the_fill_history_query_fails(service, broker):
    broker.inquire_filled_history = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None
    ))

    assert await service.reconcile_orders_with_broker() == 0
    assert any("체결내역 조회 실패" in str(c)
               for c in service.logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_reconcile_stops_when_the_balance_query_fails(service, broker):
    broker.get_account_balance = AsyncMock(return_value=None)

    assert await service.reconcile_orders_with_broker() == 0
    assert any("잔고 조회 실패" in str(c)
               for c in service.logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_reconcile_tolerates_unparsable_quantities_in_broker_rows(service, broker):
    broker.inquire_filled_history = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="", data={"output": [{"odno": "1", "tot_ccld_qty": "숫자아님"}]},
    ))
    broker.get_account_balance = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="", data={"output1": [{"pdno": "005930", "hldg_qty": "숫자아님"},
                                              {"pdno": "", "hldg_qty": "10"}]},
    ))

    assert await service.reconcile_orders_with_broker() == 0


# --- 취소 확정 위임 -----------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_marking_delegates_to_the_shared_resolver(service, mocker):
    resolve = mocker.patch.object(service, "resolve_submitted_order", AsyncMock())

    await service.mark_order_canceled("005930", True, exchange=Exchange.NXT)

    resolve.assert_awaited_once_with(
        "005930", True, exchange=Exchange.NXT, final_state=OrderState.CANCELED
    )


@pytest.mark.asyncio
async def test_rejection_is_ignored_for_unknown_or_terminal_orders(service, fsm):
    assert await service.mark_order_rejected("005930", True) is None

    context = fsm.register(_context(state=OrderState.FILLED))
    assert await service.mark_order_rejected("005930", True) is context
