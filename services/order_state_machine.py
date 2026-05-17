import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from common.types import (
    Exchange,
    OrderContext,
    OrderExecutionReport,
    OrderSide,
    OrderState,
    ResCommonResponse,
)


class OrderStateMachine:
    """주문 FSM 상태 관리자.

    Phase 3 (plan: 3-3-playful-walrus.md)에서 OrderExecutionService에서 분리.
    순수 FSM 책임만 보유한다: 상태 등록·전이·조회, symbol/order_key 잠금,
    intent dedup, execution event dedup, fast-poll 윈도우 추적.

    외부 부수효과(broker call, notification 발행, polling, virtual trade 기록)는
    소유하지 않는다. 단, terminal state 도달 시 deferred_queue 해제 트리거는
    FSM 응집성 측면에서 필수 결합이므로 포함한다.
    """

    _FAST_POLL_INTERVAL_SEC = 5
    _DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC = 15
    _POST_SUBMIT_FAST_POLL_WINDOW_SEC = 60
    _PROCESSED_EXECUTION_EVENT_LIMIT = 5000

    def __init__(
        self,
        logger,
        *,
        now_provider: Optional[Callable[[], datetime]] = None,
        deferred_queue=None,
        notification_tasks: Optional[set] = None,
        processed_execution_event_limit: Optional[int] = None,
        post_submit_fast_poll_window_sec: Optional[int] = None,
        fast_poll_interval_sec: Optional[int] = None,
        default_active_poll_interval_sec: Optional[int] = None,
    ) -> None:
        self.logger = logger
        self._now: Callable[[], datetime] = now_provider or datetime.now
        self._deferred_queue = deferred_queue
        self._post_submit_fast_poll_window_sec = (
            post_submit_fast_poll_window_sec or self._POST_SUBMIT_FAST_POLL_WINDOW_SEC
        )
        self._fast_poll_interval_sec = fast_poll_interval_sec or self._FAST_POLL_INTERVAL_SEC
        self._default_active_poll_interval_sec = (
            default_active_poll_interval_sec or self._DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC
        )

        self._order_states: Dict[str, OrderContext] = {}
        self._order_locks: Dict[str, asyncio.Lock] = {}
        self._order_no_index: Dict[str, str] = {}
        self._intent_index: Dict[str, str] = {}
        self._processed_execution_events: "OrderedDict[str, None]" = OrderedDict()
        self._processed_execution_event_limit = (
            processed_execution_event_limit or self._PROCESSED_EXECUTION_EVENT_LIMIT
        )
        self._post_submit_fast_poll_until: Dict[str, datetime] = {}
        # OES와 deferred-release task 추적 set을 공유 (테스트가 handler._notification_tasks로 await 가능)
        self._notification_tasks: set[asyncio.Task] = notification_tasks if notification_tasks is not None else set()
        self._reconcile_mismatch_count: int = 0

    # ── key / lock helpers ────────────────────────────────────────────
    @staticmethod
    def make_order_key(stock_code: str, side: OrderSide, exchange: Exchange) -> str:
        return f"{exchange.value}:{stock_code}:{side.value}"

    @staticmethod
    def make_symbol_lock_key(stock_code: str, exchange: Exchange) -> str:
        return f"{exchange.value}:{stock_code}"

    def symbol_lock(self, stock_code: str, exchange: Exchange) -> asyncio.Lock:
        lock_key = self.make_symbol_lock_key(stock_code, exchange)
        current_loop = asyncio.get_running_loop()
        lock = self._order_locks.get(lock_key)
        lock_loop = getattr(lock, "_loop", None) if lock else None
        if lock is None or (lock_loop is not None and lock_loop is not current_loop):
            lock = asyncio.Lock()
            self._order_locks[lock_key] = lock
        return lock

    # ── lookups ───────────────────────────────────────────────────────
    def lookup(self, order_key: str) -> Optional[OrderContext]:
        return self._order_states.get(order_key)

    def lookup_by_side(
        self,
        stock_code: str,
        side: OrderSide,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return self._order_states.get(self.make_order_key(stock_code, side, exchange))

    def lookup_by_buy_flag(
        self,
        stock_code,
        is_buy: bool,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        return self.lookup_by_side(stock_code, side, exchange)

    def has_active(self, stock_code, exchange: Exchange = Exchange.KRX) -> bool:
        for side in (OrderSide.BUY, OrderSide.SELL):
            context = self.lookup_by_side(stock_code, side, exchange)
            if context and not context.state.is_terminal:
                return True
        return False

    @staticmethod
    def extract_broker_order_no(result: ResCommonResponse) -> Optional[str]:
        if not result or not result.data:
            return None
        if hasattr(result.data, "ordno"):
            return result.data.ordno
        if isinstance(result.data, dict):
            return result.data.get("ordno") or result.data.get("order_no") or result.data.get("odno")
        return None

    # ── register / transition ─────────────────────────────────────────
    def register(self, context: OrderContext) -> OrderContext:
        now = self._now()
        if context.created_at is None or context.state_entered_at is None:
            context = context.model_copy(update={
                "created_at": context.created_at or now,
                "state_entered_at": context.state_entered_at or now,
            })
        self._order_states[context.order_key] = context
        if context.broker_order_no:
            self._order_no_index[context.broker_order_no] = context.order_key
        return context

    def transition(self, order_key: str, new_state: OrderState, **kwargs) -> OrderContext:
        context = self._order_states[order_key]
        next_context = context.transition(
            new_state,
            transition_time=self._now(),
            **kwargs,
        )
        self._order_states[order_key] = next_context
        if next_context.broker_order_no:
            self._order_no_index[next_context.broker_order_no] = order_key
        if next_context.state.is_terminal and next_context.intent_id:
            self._intent_index.pop(next_context.intent_id, None)
        if next_context.state.is_terminal:
            self._schedule_deferred_release(next_context.stock_code)
        return next_context

    def safe_transition(
        self, order_key: str, new_state: OrderState, **kwargs
    ) -> Optional[OrderContext]:
        """외부 이벤트(broker 응답, reconcile, WebSocket) 로 트리거된 상태 전이에 사용.
        invalid transition 은 raise 하지 않고 WARNING + no-op 으로 처리한다.
        내부 개발 오류성 전이는 transition 을 직접 사용해 ValueError 를 유지한다.
        """
        try:
            return self.transition(order_key, new_state, **kwargs)
        except (ValueError, KeyError) as e:
            context = self._order_states.get(order_key)
            current_state = context.state.value if context else "unknown"
            self._reconcile_mismatch_count += 1
            self.logger.warning(
                f"외부 이벤트 상태 전이 실패(no-op): order_key={order_key}, "
                f"current={current_state}, requested={new_state.value}, error={e}, "
                f"mismatch_count={self._reconcile_mismatch_count}"
            )
            return context

    def _schedule_deferred_release(self, stock_code: str) -> None:
        """terminal state 도달 시 deferred order queue 해제를 비동기로 트리거.

        symbol lock 점유 중에 호출될 수 있으므로 create_task 로 분리한다 (deadlock 방지).
        """
        if self._deferred_queue is None or not stock_code:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._deferred_queue.notify_terminal(stock_code))
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)

    def mark_virtual_trade_recorded(
        self, context: OrderContext, recorded_qty: int
    ) -> OrderContext:
        next_context = context.model_copy(update={
            "virtual_recorded_qty": min(max(recorded_qty, 0), context.filled_qty),
        })
        self._order_states[context.order_key] = next_context
        if next_context.broker_order_no:
            self._order_no_index[next_context.broker_order_no] = context.order_key
        return next_context

    # ── execution event dedup ────────────────────────────────────────
    def mark_execution_event_seen(self, event_key: str) -> bool:
        """Return True only for an event key that has not been processed recently."""
        if event_key in self._processed_execution_events:
            self._processed_execution_events.move_to_end(event_key)
            return False
        limit = self._processed_execution_event_limit
        if limit <= 0:
            self._processed_execution_events.clear()
            return True
        self._processed_execution_events[event_key] = None
        while len(self._processed_execution_events) > limit:
            self._processed_execution_events.popitem(last=False)
        return True

    def find_context_for_execution_report(
        self, report: OrderExecutionReport
    ) -> Optional[OrderContext]:
        order_key = self._order_no_index.get(report.broker_order_no)
        if order_key:
            return self._order_states.get(order_key)
        if report.side:
            return self.lookup_by_side(report.stock_code, report.side, report.exchange)
        for side in (OrderSide.BUY, OrderSide.SELL):
            context = self.lookup_by_side(report.stock_code, side, report.exchange)
            if context and context.broker_order_no == report.broker_order_no:
                return context
        return None

    # ── active orders / summary / fast poll ──────────────────────────
    def active_contexts(self) -> list[OrderContext]:
        return [
            context
            for context in self._order_states.values()
            if not context.state.is_terminal
        ]

    def active_summary(self) -> dict:
        """reconcile_alarm을 제외한 활성 주문 요약. reconcile_alarm은 Phase 4 소유."""
        contexts = self.active_contexts()
        return {
            "active_order_count": len(contexts),
            "unfilled_order_count": sum(1 for c in contexts if c.remaining_qty > 0),
            "orders": [
                {
                    "order_key": c.order_key,
                    "stock_code": c.stock_code,
                    "side": c.side.value,
                    "state": c.state.value,
                    "qty": c.qty,
                    "filled_qty": c.filled_qty,
                    "remaining_qty": c.remaining_qty,
                    "broker_order_no": c.broker_order_no,
                }
                for c in contexts
            ],
            "reconcile_mismatch_count": self._reconcile_mismatch_count,
        }

    def register_post_submit_fast_poll(
        self, order_key: str, now: Optional[datetime] = None
    ) -> None:
        now = now or self._now()
        self._post_submit_fast_poll_until[order_key] = now + timedelta(
            seconds=self._post_submit_fast_poll_window_sec
        )

    def prune_post_submit_fast_poll(self, now: Optional[datetime] = None) -> None:
        now = now or self._now()
        active_order_keys = {
            context.order_key
            for context in self._order_states.values()
            if not context.state.is_terminal
        }
        stale_keys = [
            order_key
            for order_key, until in self._post_submit_fast_poll_until.items()
            if order_key not in active_order_keys or until <= now
        ]
        for order_key in stale_keys:
            self._post_submit_fast_poll_until.pop(order_key, None)

    def get_active_order_poll_interval_sec(
        self,
        now: Optional[datetime] = None,
        *,
        default_interval_sec: Optional[int] = None,
    ) -> Optional[int]:
        default_interval_sec = (
            default_interval_sec
            if default_interval_sec is not None
            else self._default_active_poll_interval_sec
        )
        now = now or self._now()
        contexts = self.active_contexts()
        if not contexts:
            self.prune_post_submit_fast_poll(now)
            return None
        self.prune_post_submit_fast_poll(now)
        active_order_keys = {context.order_key for context in contexts}
        if any(order_key in self._post_submit_fast_poll_until for order_key in active_order_keys):
            return min(default_interval_sec, self._fast_poll_interval_sec)
        return default_interval_sec

    # ── intent index ─────────────────────────────────────────────────
    def register_intent(self, intent_id: str, order_key: str) -> None:
        self._intent_index[intent_id] = order_key

    def intent_to_order_key(self, intent_id: str) -> Optional[str]:
        return self._intent_index.get(intent_id)

    def release_intent(self, intent_id: str) -> None:
        self._intent_index.pop(intent_id, None)
