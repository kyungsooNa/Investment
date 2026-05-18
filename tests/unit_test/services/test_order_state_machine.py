import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    Exchange,
    OrderContext,
    OrderExecutionReport,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from services.order_state_machine import OrderStateMachine


class _Logger:
    def __init__(self):
        self.info = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.debug = MagicMock()


def _make_fsm(*, deferred_queue=None, now: datetime | None = None) -> OrderStateMachine:
    return OrderStateMachine(
        logger=_Logger(),
        now_provider=(lambda: now) if now else None,
        deferred_queue=deferred_queue,
    )


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


# ── key helpers ────────────────────────────────────────────────────────────

def test_make_order_key_combines_exchange_code_side():
    assert OrderStateMachine.make_order_key("005930", OrderSide.BUY, Exchange.KRX) == "KRX:005930:BUY"
    assert OrderStateMachine.make_order_key("005930", OrderSide.SELL, Exchange.NXT) == "NXT:005930:SELL"


def test_make_symbol_lock_key_excludes_side():
    assert OrderStateMachine.make_symbol_lock_key("005930", Exchange.KRX) == "KRX:005930"


@pytest.mark.asyncio
async def test_symbol_lock_returns_same_lock_across_calls():
    fsm = _make_fsm()
    lock_a = fsm.symbol_lock("005930", Exchange.KRX)
    lock_b = fsm.symbol_lock("005930", Exchange.KRX)
    assert lock_a is lock_b


@pytest.mark.asyncio
async def test_symbol_lock_distinguishes_codes():
    fsm = _make_fsm()
    lock_a = fsm.symbol_lock("005930", Exchange.KRX)
    lock_b = fsm.symbol_lock("000660", Exchange.KRX)
    assert lock_a is not lock_b


# ── register / lookup ─────────────────────────────────────────────────────

def test_register_sets_created_at_and_state_entered_at_when_missing():
    fixed_now = datetime(2026, 5, 17, 9, 0, 0)
    fsm = _make_fsm(now=fixed_now)
    context = _make_context()
    registered = fsm.register(context)
    assert registered.created_at == fixed_now
    assert registered.state_entered_at == fixed_now
    assert fsm.lookup(context.order_key) is registered


def test_register_indexes_broker_order_no_when_set():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    context = _make_context(broker_order_no="O0001")
    fsm.register(context)
    assert fsm._order_no_index["O0001"] == context.order_key


def test_lookup_by_side_and_buy_flag_agree():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    ctx = fsm.register(_make_context())
    assert fsm.lookup_by_side("005930", OrderSide.BUY, Exchange.KRX) is ctx
    assert fsm.lookup_by_buy_flag("005930", True, Exchange.KRX) is ctx
    assert fsm.lookup_by_side("005930", OrderSide.SELL, Exchange.KRX) is None


def test_has_active_returns_true_only_for_non_terminal():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    assert fsm.has_active("005930", Exchange.KRX) is True
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED)
    fsm.transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)
    assert fsm.has_active("005930", Exchange.KRX) is False


# ── extract_broker_order_no ───────────────────────────────────────────────

def test_extract_broker_order_no_from_dict_variants():
    assert OrderStateMachine.extract_broker_order_no(None) is None
    assert OrderStateMachine.extract_broker_order_no(ResCommonResponse(rt_cd="0", msg1="", data=None)) is None
    assert OrderStateMachine.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"ordno": "X"})
    ) == "X"
    assert OrderStateMachine.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"order_no": "Y"})
    ) == "Y"
    assert OrderStateMachine.extract_broker_order_no(
        ResCommonResponse(rt_cd="0", msg1="", data={"odno": "Z"})
    ) == "Z"


# ── transition ────────────────────────────────────────────────────────────

def test_transition_updates_state_and_no_index():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    after = fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED, broker_order_no="O0001")
    assert after.state == OrderState.SUBMITTED
    assert after.broker_order_no == "O0001"
    assert fsm._order_no_index["O0001"] == "KRX:005930:BUY"


def test_transition_clears_intent_on_terminal_state():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    ctx = _make_context(intent_id="intent-123")
    fsm.register(ctx)
    fsm.register_intent("intent-123", ctx.order_key)
    fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)
    assert fsm.intent_to_order_key("intent-123") is None


def test_transition_raises_for_invalid_transition():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    # FILLED 직접 점프는 OrderContext.transition 검증을 받는다 (PENDING_SUBMIT → FILLED 일반 불가)
    with pytest.raises(ValueError):
        fsm.transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)


# ── safe_transition ──────────────────────────────────────────────────────

def test_safe_transition_returns_context_and_logs_on_invalid():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    result = fsm.safe_transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)
    # raise 없이 현재 context 반환
    assert result is not None
    assert result.state == OrderState.PENDING_SUBMIT
    assert fsm._reconcile_mismatch_count == 1
    fsm.logger.warning.assert_called()


def test_safe_transition_returns_none_for_unknown_key():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    result = fsm.safe_transition("KRX:UNKNOWN:BUY", OrderState.SUBMITTED)
    assert result is None
    assert fsm._reconcile_mismatch_count == 1


# ── deferred queue release ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_transition_schedules_deferred_release():
    queue = AsyncMock()
    queue.notify_terminal = AsyncMock()
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0), deferred_queue=queue)
    fsm.register(_make_context())
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED)
    fsm.transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)

    if fsm._notification_tasks:
        await asyncio.gather(*list(fsm._notification_tasks))
    queue.notify_terminal.assert_awaited_once_with("005930")


# ── mark_execution_event_seen (dedup) ─────────────────────────────────────

def test_mark_execution_event_seen_dedups_repeated_keys():
    fsm = _make_fsm()
    assert fsm.mark_execution_event_seen("E1") is True
    assert fsm.mark_execution_event_seen("E1") is False
    assert fsm.mark_execution_event_seen("E2") is True


def test_mark_execution_event_seen_enforces_limit():
    fsm = OrderStateMachine(logger=_Logger(), processed_execution_event_limit=2)
    fsm.mark_execution_event_seen("A")
    fsm.mark_execution_event_seen("B")
    fsm.mark_execution_event_seen("C")
    # "A"는 오래되어 evict됨 → 재추가 가능
    assert "A" not in fsm._processed_execution_events
    assert fsm.mark_execution_event_seen("A") is True


# ── find_context_for_execution_report ────────────────────────────────────

def test_find_context_by_broker_order_no_via_index():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    ctx = fsm.register(_make_context(broker_order_no="O0001"))
    report = OrderExecutionReport(broker_order_no="O0001", stock_code="005930", side=OrderSide.BUY)
    assert fsm.find_context_for_execution_report(report) is ctx


def test_find_context_by_side_fallback_when_no_index_match():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    ctx = fsm.register(_make_context())  # broker_order_no 없음
    report = OrderExecutionReport(broker_order_no="O0001", stock_code="005930", side=OrderSide.BUY)
    assert fsm.find_context_for_execution_report(report) is ctx


# ── active_contexts / active_summary ─────────────────────────────────────

def test_active_contexts_excludes_terminal():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    fsm.register(_make_context(order_key="KRX:000660:BUY", stock_code="000660"))
    fsm.transition("KRX:005930:BUY", OrderState.SUBMITTED)
    fsm.transition("KRX:005930:BUY", OrderState.FILLED, filled_qty=10)

    active = fsm.active_contexts()
    codes = {c.stock_code for c in active}
    assert codes == {"000660"}


def test_active_summary_omits_reconcile_alarm():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    fsm.register(_make_context())
    summary = fsm.active_summary()
    assert summary["active_order_count"] == 1
    assert "reconcile_alarm" not in summary  # Phase 4 소유, FSM은 미포함
    assert summary["reconcile_mismatch_count"] == 0


# ── post submit fast poll ────────────────────────────────────────────────

def test_register_then_prune_post_submit_fast_poll():
    now = datetime(2026, 5, 17, 9, 0, 0)
    fsm = _make_fsm(now=now)
    fsm.register(_make_context())
    fsm.register_post_submit_fast_poll("KRX:005930:BUY", now)
    assert "KRX:005930:BUY" in fsm._post_submit_fast_poll_until

    # window 만료 후 prune
    fsm.prune_post_submit_fast_poll(now + timedelta(seconds=120))
    assert "KRX:005930:BUY" not in fsm._post_submit_fast_poll_until


def test_get_active_order_poll_interval_returns_none_when_no_active():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    assert fsm.get_active_order_poll_interval_sec() is None


def test_get_active_order_poll_interval_uses_fast_when_in_window():
    now = datetime(2026, 5, 17, 9, 0, 0)
    fsm = _make_fsm(now=now)
    fsm.register(_make_context())
    fsm.register_post_submit_fast_poll("KRX:005930:BUY", now)
    interval = fsm.get_active_order_poll_interval_sec(now)
    assert interval == 5  # FAST_POLL_INTERVAL_SEC


def test_get_active_order_poll_interval_uses_default_outside_window():
    now = datetime(2026, 5, 17, 9, 0, 0)
    fsm = _make_fsm(now=now)
    fsm.register(_make_context())
    # fast poll 등록하지 않음
    interval = fsm.get_active_order_poll_interval_sec(now)
    assert interval == 15  # DEFAULT


# ── intent index ─────────────────────────────────────────────────────────

def test_intent_index_register_and_lookup_and_release():
    fsm = _make_fsm()
    fsm.register_intent("intent-1", "KRX:005930:BUY")
    assert fsm.intent_to_order_key("intent-1") == "KRX:005930:BUY"
    fsm.release_intent("intent-1")
    assert fsm.intent_to_order_key("intent-1") is None


# ── mark_virtual_trade_recorded ──────────────────────────────────────────

def test_mark_virtual_trade_recorded_clamps_to_filled_qty():
    fsm = _make_fsm(now=datetime(2026, 5, 17, 9, 0, 0))
    ctx = fsm.register(_make_context())
    after_submitted = fsm.transition(ctx.order_key, OrderState.SUBMITTED)
    after_filled = fsm.transition(ctx.order_key, OrderState.FILLED, filled_qty=10)
    # recorded_qty가 filled_qty를 초과하면 filled_qty로 클램프
    capped = fsm.mark_virtual_trade_recorded(after_filled, 99)
    assert capped.virtual_recorded_qty == 10
    # 음수는 0으로 클램프
    floored = fsm.mark_virtual_trade_recorded(capped, -5)
    assert floored.virtual_recorded_qty == 0
