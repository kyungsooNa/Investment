from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, OrderSide, OrderState, ResCommonResponse
from services.broker_order_submitter import BrokerOrderSubmitter
from services.execution_quality_reporter import ExecutionQualityReporter
from services.fill_reconciliation_service import FillReconciliationService
from services.order_state_machine import OrderStateMachine
from services.order_submission_coordinator import OrderSubmissionCoordinator


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.debug = MagicMock()


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
    mock.env = MagicMock(is_paper_trading=True)
    mock.place_stock_order = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="주문 성공", data={"ordno": "C0001"},
    ))
    mock.cancel_stock_order = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data=None))
    mock.inquire_unfilled_orders = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output": []}))
    mock.inquire_filled_history = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output": []}))
    mock.get_account_balance = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="", data={"output1": []}))
    return mock


@pytest.fixture
def submitter(broker, fsm):
    return BrokerOrderSubmitter(
        broker_api_wrapper=broker,
        logger=_Logger(),
        state_provider=lambda: fsm._order_states,
        transition_fn=fsm.transition,
        extract_broker_order_no_fn=OrderStateMachine.extract_broker_order_no,
        max_retries=1,
        retry_delay_sec=0,
    )


@pytest.fixture
def fill_reconciliation(broker, fsm, reporter, fixed_now):
    return FillReconciliationService(
        broker_api_wrapper=broker,
        logger=_Logger(),
        state_machine=fsm,
        execution_quality_reporter=reporter,
        now_provider=lambda: fixed_now,
        is_paper_trading_fn=lambda: True,
    )


def _make_coordinator(*, broker, fsm, reporter, submitter, fill_reconciliation,
                     deferred_queue=None, data_quality=None, order_policy=None,
                     risk_gate=None, notification=None, paper=True) -> OrderSubmissionCoordinator:
    return OrderSubmissionCoordinator(
        logger=_Logger(),
        broker_api_wrapper=broker,
        state_machine=fsm,
        broker_submitter=submitter,
        execution_quality_reporter=reporter,
        fill_reconciliation_service=fill_reconciliation,
        deferred_order_queue=deferred_queue,
        data_quality_service=data_quality,
        order_policy_service=order_policy,
        risk_gate_service=risk_gate,
        notification_service=notification,
        is_paper_trading_fn=lambda: paper,
    )


# ── happy path ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_happy_path_registers_context_and_submits(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation)
    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=True,
    )
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 컨텍스트 등록되고 FILLED 까지 전이됨 (paper mode + finalize_immediately=True)
    ctx = fsm.lookup("KRX:005930:BUY")
    assert ctx is not None
    assert ctx.state == OrderState.FILLED


# ── reconcile_alarm gate ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocked_when_reconcile_alarm_active(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation)
    fill_reconciliation._reconcile_alarm = True

    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=False,
    )
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "reconcile alarm" in result.msg1
    broker.place_stock_order.assert_not_awaited()


# ── duplicate intent dedup ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocks_duplicate_intent_id(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation)
    iid = "intent-xyz"
    first = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:a", finalize_immediately=False, intent_id=iid,
    )
    # 첫 호출 후 컨텍스트는 SUBMITTED (paper + finalize=False)
    assert first.rt_cd == ErrorCode.SUCCESS.value

    # 동일 intent_id 로 다른 종목 시도 → 차단
    second = await coord.submit(
        stock_code="000660", price=60000, qty=5,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:b", finalize_immediately=False, intent_id=iid,
    )
    assert second.rt_cd == ErrorCode.API_ERROR.value
    assert "duplicate intent" in second.msg1


# ── bidirectional block ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocks_when_opposite_side_active(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation)
    buy = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:a", finalize_immediately=False,
    )
    assert buy.rt_cd == ErrorCode.SUCCESS.value

    sell = await coord.submit(
        stock_code="005930", price=70000, qty=5,
        exchange=Exchange.KRX, side=OrderSide.SELL,
        source="strategy:b", finalize_immediately=False,
    )
    assert sell.rt_cd == ErrorCode.RETRY_LIMIT.value
    assert "진행 중인 주문" in sell.msg1


# ── data quality block ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocks_on_data_quality_failure(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    data_quality = AsyncMock()
    quality_result = MagicMock()
    quality_result.ok = False
    quality_result.reason = "old price"
    quality_result.severity = "error"
    quality_result.latency_sec = 1.0
    quality_result.metadata = {}
    quality_result.to_dict = MagicMock(return_value={"reason": "old price"})
    data_quality.validate_order_reference = AsyncMock(return_value=quality_result)

    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation,
                              data_quality=data_quality)

    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=False,
    )
    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.place_stock_order.assert_not_awaited()


# ── order policy block + price adjustment ──────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocked_by_order_policy(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    order_policy = AsyncMock()
    decision = MagicMock()
    decision.blocked = True
    decision.to_response = MagicMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="policy block", data=None,
    ))
    order_policy.validate_order = AsyncMock(return_value=decision)

    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation,
                              order_policy=order_policy)

    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=False,
    )
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.msg1 == "policy block"


# ── risk gate block ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_blocked_by_risk_gate(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    risk_gate = AsyncMock()
    risk_gate.validate_order = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="risk block", data=None,
    ))
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation,
                              risk_gate=risk_gate)

    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=False,
    )
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "risk block" in result.msg1


# ── broker failure → REJECTED transition ────────────────────────────────

@pytest.mark.asyncio
async def test_submit_marks_rejected_on_broker_failure(
    broker, fsm, reporter, submitter, fill_reconciliation
):
    broker.place_stock_order.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="잔고부족", data=None,
    )
    coord = _make_coordinator(broker=broker, fsm=fsm, reporter=reporter,
                              submitter=submitter, fill_reconciliation=fill_reconciliation)

    result = await coord.submit(
        stock_code="005930", price=70000, qty=10,
        exchange=Exchange.KRX, side=OrderSide.BUY,
        source="strategy:test", finalize_immediately=False,
    )
    assert result.rt_cd == ErrorCode.API_ERROR.value
    ctx = fsm.lookup("KRX:005930:BUY")
    assert ctx is not None
    assert ctx.state == OrderState.REJECTED


# ── _resolve_finalize policy ───────────────────────────────────────────

def test_resolve_finalize_paper_mode_returns_requested():
    coord = OrderSubmissionCoordinator(
        logger=_Logger(),
        broker_api_wrapper=MagicMock(),
        state_machine=MagicMock(),
        broker_submitter=MagicMock(),
        execution_quality_reporter=MagicMock(),
        fill_reconciliation_service=MagicMock(),
        is_paper_trading_fn=lambda: True,
    )
    assert coord._resolve_finalize(True) is True
    assert coord._resolve_finalize(False) is False


def test_resolve_finalize_real_mode_always_false():
    coord = OrderSubmissionCoordinator(
        logger=_Logger(),
        broker_api_wrapper=MagicMock(),
        state_machine=MagicMock(),
        broker_submitter=MagicMock(),
        execution_quality_reporter=MagicMock(),
        fill_reconciliation_service=MagicMock(),
        is_paper_trading_fn=lambda: False,
    )
    assert coord._resolve_finalize(True) is False
    assert coord._resolve_finalize(False) is False
