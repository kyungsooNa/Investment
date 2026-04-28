# app/order_execution_service.py
import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, Optional
from common.types import ErrorCode, ResCommonResponse, Exchange, OrderContext, OrderSide, OrderState, OrderExecutionReport
from core.retry_queue.retry_classifier import classify, RequestOutcome
from core.loggers.trace_context import trace_scope, get_trace_id, new_trace_id
from core.performance_profiler import PerformanceProfiler
from core.market_clock import MarketClock
from repositories.streaming_stock_repo import StreamingType
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel
from services.market_calendar_service import MarketCalendarService
from services.price_subscription_service import SubscriptionPriority
from services.kill_switch_service import KillSwitchService
from services.risk_gate_service import RiskGateService
from services.order_policy_service import OrderPolicyDecision, OrderPolicyService
from core.account_snapshot import AccountSnapshotCache


class OrderExecutionService:
    """
    주식 매수/매도 주문 및 실시간 체결가/호가 구독 관련 핸들러를 관리하는 클래스입니다.
    TradingService, Logger, MarketClock 인스턴스를 주입받아 사용합니다.
    """

    _ORDER_MAX_RETRIES = 3
    _ORDER_RETRY_DELAY_SEC = 3
    _PROCESSED_EXECUTION_EVENT_LIMIT = 5000
    _FAST_POLL_INTERVAL_SEC = 5
    _DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC = 15
    _POST_SUBMIT_FAST_POLL_WINDOW_SEC = 60
    _STUCK_ORDER_WARNING_SEC = 60
    _STUCK_ORDER_CRITICAL_SEC = 180

    def __init__(self, broker_api_wrapper, logger,
                 market_clock: Optional[MarketClock] = None,
                 performance_profiler: Optional[PerformanceProfiler] = None,
                 notification_service: Optional[NotificationService] = None,
                 market_calendar_service: Optional[MarketCalendarService] = None,
                 price_subscription_service=None,
                 virtual_trade_service=None,
                 kill_switch_service: Optional[KillSwitchService] = None,
                 account_snapshot_cache: Optional[AccountSnapshotCache] = None,
                 risk_gate_service: Optional[RiskGateService] = None,
                 order_policy_service: Optional[OrderPolicyService] = None):
        self.broker_api_wrapper = broker_api_wrapper
        self.logger = logger
        self.market_clock = market_clock
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._notification_service = notification_service
        self.market_calendar_service = market_calendar_service
        self._price_sub_svc = price_subscription_service
        self._virtual_trade_service = virtual_trade_service
        self._kill_switch = kill_switch_service
        self._account_snapshot_cache = account_snapshot_cache
        self._risk_gate = risk_gate_service
        self._order_policy = order_policy_service
        self._order_states: Dict[str, OrderContext] = {}
        self._order_locks: Dict[str, asyncio.Lock] = {}
        self._order_no_index: Dict[str, str] = {}
        self._processed_execution_events: OrderedDict[str, None] = OrderedDict()
        self._processed_execution_event_limit = self._PROCESSED_EXECUTION_EVENT_LIMIT
        self._post_submit_fast_poll_until: Dict[str, datetime] = {}
        self._reconcile_mismatch_count: int = 0
        self._reconcile_alarm: bool = False
        self._reconcile_consecutive_mismatch_by_key: Dict[str, int] = {}
        self._intent_index: Dict[str, str] = {}  # intent_id → order_key

    def _get_now(self) -> datetime:
        return self.market_clock.get_current_kst_time() if self.market_clock else datetime.now()

    def _is_paper_trading_mode(self) -> bool:
        env = getattr(self.broker_api_wrapper, "env", None)
        return getattr(env, "is_paper_trading", True)

    def _log_real_order_preview(
        self,
        *,
        stock_code: str,
        side: OrderSide,
        price,
        qty,
        exchange: Exchange,
        source: str,
        trace_id,
        intent_id: str,
        order_key: str,
    ) -> None:
        """실전 모드에서 broker API 호출 직전 요청 payload 요약을 INFO 로그로 남김.

        사후 감사용. 모의투자 모드에서는 no-op.
        민감정보(전체 계좌번호, 토큰)는 prefix만 출력한다.
        """
        if self._is_paper_trading_mode():
            return

        env = getattr(self.broker_api_wrapper, "env", None)
        active_cfg = getattr(env, "active_config", None) or {}
        account = active_cfg.get("stock_account_number") if isinstance(active_cfg, dict) else None
        account_prefix = (str(account)[:4] + "...") if account else "unknown"
        base_url = getattr(env, "_base_url", None) or ""
        url_host = base_url.split("//", 1)[-1].split("/", 1)[0] if base_url else "unknown"

        try:
            self.logger.info(
                f"[REAL ORDER PREVIEW] order_key={order_key} code={stock_code} "
                f"side={side.value} qty={qty} price={price} exchange={exchange.value} "
                f"account_prefix={account_prefix} url_host={url_host} "
                f"source={source} trace_id={trace_id} intent_id={intent_id}"
            )
        except Exception as exc:
            self.logger.warning(f"[REAL ORDER PREVIEW] 로그 작성 실패: {exc}")

    def _resolve_finalize(self, requested: bool) -> bool:
        """paper mode 에서는 caller 값 그대로, real mode 에서는 항상 False."""
        if self._is_paper_trading_mode():
            return requested
        if requested:
            self.logger.warning(
                "실전 모드: finalize_immediately=True 요청을 무시합니다. "
                "체결 확정은 WebSocket/polling 체결 통보로만 처리됩니다."
            )
        return False

    def _make_order_key(self, stock_code: str, side: OrderSide, exchange: Exchange) -> str:
        return f"{exchange.value}:{stock_code}:{side.value}"

    def _make_symbol_lock_key(self, stock_code: str, exchange: Exchange) -> str:
        return f"{exchange.value}:{stock_code}"

    def _get_symbol_lock(self, stock_code: str, exchange: Exchange) -> asyncio.Lock:
        lock_key = self._make_symbol_lock_key(stock_code, exchange)
        current_loop = asyncio.get_running_loop()
        lock = self._order_locks.get(lock_key)
        lock_loop = getattr(lock, "_loop", None) if lock else None
        if lock is None or (lock_loop is not None and lock_loop is not current_loop):
            lock = asyncio.Lock()
            self._order_locks[lock_key] = lock
        return lock

    def _get_order_context_by_side(
        self,
        stock_code: str,
        side: OrderSide,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return self._order_states.get(self._make_order_key(stock_code, side, exchange))

    def get_order_context(self, stock_code, is_buy: bool, exchange: Exchange = Exchange.KRX) -> Optional[OrderContext]:
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        return self._get_order_context_by_side(stock_code, side, exchange)

    def has_active_order(self, stock_code, exchange: Exchange = Exchange.KRX) -> bool:
        for side in (OrderSide.BUY, OrderSide.SELL):
            context = self._get_order_context_by_side(stock_code, side, exchange)
            if context and not context.state.is_terminal:
                return True
        return False

    def _extract_broker_order_no(self, result: ResCommonResponse) -> Optional[str]:
        if not result or not result.data:
            return None
        if hasattr(result.data, "ordno"):
            return result.data.ordno
        if isinstance(result.data, dict):
            return result.data.get("ordno") or result.data.get("order_no") or result.data.get("odno")
        return None

    def _set_order_context(self, context: OrderContext) -> OrderContext:
        now = self._get_now()
        if context.created_at is None or context.state_entered_at is None:
            context = context.model_copy(update={
                "created_at": context.created_at or now,
                "state_entered_at": context.state_entered_at or now,
            })
        self._order_states[context.order_key] = context
        if context.broker_order_no:
            self._order_no_index[context.broker_order_no] = context.order_key
        return context

    def _resolve_expected_fill_price(
        self,
        price: int,
        policy_decision: Optional[OrderPolicyDecision],
    ) -> Optional[int]:
        if price > 0:
            return price
        if policy_decision is None:
            return None
        context = policy_decision.context or {}
        expected = context.get("executable_price") or context.get("reference_price")
        try:
            return int(expected) if expected else None
        except (TypeError, ValueError):
            return None

    def _build_execution_quality_update(
        self,
        context: OrderContext,
        report: OrderExecutionReport,
        filled_qty: int,
    ) -> dict:
        fill_delta_qty = max(filled_qty - context.filled_qty, 0)
        fill_price = report.fill_price or context.average_fill_price or context.price
        fill_amount_delta = int(fill_price * fill_delta_qty) if fill_price and fill_delta_qty else 0
        total_fill_amount = context.total_fill_amount + fill_amount_delta
        average_fill_price = (
            total_fill_amount / filled_qty
            if filled_qty > 0 and total_fill_amount > 0
            else context.average_fill_price
        )
        expected = context.expected_fill_price or (context.price if context.price > 0 else None)

        slippage_amount = context.slippage_amount_won
        slippage_pct = context.slippage_pct
        if expected and average_fill_price:
            if context.side == OrderSide.BUY:
                slippage_amount = average_fill_price - expected
            else:
                slippage_amount = expected - average_fill_price
            slippage_pct = slippage_amount / expected * 100

        first_fill_latency = context.first_fill_latency_sec
        if first_fill_latency is None and context.created_at is not None and fill_delta_qty > 0:
            first_fill_latency = (self._get_now() - context.created_at).total_seconds()

        return {
            "average_fill_price": average_fill_price,
            "total_fill_amount": total_fill_amount,
            "last_fill_price": int(fill_price) if fill_price else None,
            "slippage_amount_won": slippage_amount,
            "slippage_pct": slippage_pct,
            "first_fill_latency_sec": first_fill_latency,
        }

    def _log_execution_quality(self, context: OrderContext) -> None:
        if context.average_fill_price is None:
            return
        self.logger.info(
            f"[EXECUTION QUALITY] order_key={context.order_key} code={context.stock_code} "
            f"side={context.side.value} expected_price={context.expected_fill_price} "
            f"avg_fill_price={context.average_fill_price:.2f} filled_qty={context.filled_qty} "
            f"slippage_won={context.slippage_amount_won} slippage_pct={context.slippage_pct} "
            f"first_fill_latency_sec={context.first_fill_latency_sec} source={context.source} "
            f"trace_id={context.trace_id}"
        )

    def _transition_order_context(self, order_key: str, new_state: OrderState, **kwargs) -> OrderContext:
        context = self._order_states[order_key]
        next_context = context.transition(
            new_state,
            transition_time=self._get_now(),
            **kwargs,
        )
        self._order_states[order_key] = next_context
        if next_context.broker_order_no:
            self._order_no_index[next_context.broker_order_no] = order_key
        if next_context.state.is_terminal and next_context.intent_id:
            self._intent_index.pop(next_context.intent_id, None)
        return next_context

    def _safe_transition_order_context(self, order_key: str, new_state: OrderState, **kwargs) -> Optional[OrderContext]:
        """외부 이벤트(broker 응답, reconcile, WebSocket) 로 트리거된 상태 전이에 사용.
        invalid transition 은 raise 하지 않고 WARNING + no-op 으로 처리한다.
        내부 개발 오류성 전이는 _transition_order_context 를 직접 사용해 ValueError 를 유지한다.
        """
        try:
            return self._transition_order_context(order_key, new_state, **kwargs)
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

    def _mark_virtual_trade_recorded(self, context: OrderContext, recorded_qty: int) -> OrderContext:
        next_context = context.model_copy(update={
            "virtual_recorded_qty": min(max(recorded_qty, 0), context.filled_qty),
        })
        self._order_states[context.order_key] = next_context
        if next_context.broker_order_no:
            self._order_no_index[next_context.broker_order_no] = context.order_key
        return next_context

    @staticmethod
    def _strategy_name_from_source(source: str) -> tuple[str, bool]:
        source = source or ""
        if source.startswith("strategy:"):
            return source.split(":", 1)[1] or "default", True
        if source.startswith("manual:"):
            return source.split(":", 1)[1] or "수동매매", False
        if source in ("", "default", "manual", "web"):
            return "수동매매", False
        return source, False

    async def _persist_virtual_trade_for_terminal_report(
        self,
        context: OrderContext,
        report: OrderExecutionReport,
    ) -> OrderContext:
        if not self._virtual_trade_service or not context.state.is_terminal:
            return context
        if context.state == OrderState.REJECTED or context.filled_qty <= 0:
            return context
        if context.virtual_recorded_qty >= context.filled_qty:
            return context

        strategy_name, is_strategy_source = self._strategy_name_from_source(context.source)
        record_qty = context.filled_qty
        record_price = report.fill_price or context.price
        if self._kill_switch and report.fill_price and context.price > 0:
            await self._kill_switch.record_fill_event(
                context.price, report.fill_price, context.stock_code, record_qty
            )
        try:
            if context.side == OrderSide.BUY:
                await self._virtual_trade_service.log_buy_async(
                    strategy_name, context.stock_code, record_price, record_qty
                )
            elif is_strategy_source:
                await self._virtual_trade_service.log_sell_by_strategy_async(
                    strategy_name, context.stock_code, record_price, record_qty
                )
            else:
                await self._virtual_trade_service.log_sell_async(
                    context.stock_code, record_price, record_qty
                )
        except Exception as e:
            self.logger.warning(
                f"체결확정 가상매매 기록 실패: 주문={context.order_key}, "
                f"수량={record_qty}, 사유={e}"
            )
            return context
        # 체결 확정 → 잔고 스냅샷 캐시 무효화 (PositionSizingService 가 다음 조회 시 refresh)
        if self._account_snapshot_cache is not None:
            self._account_snapshot_cache.invalidate()
        return self._mark_virtual_trade_recorded(context, record_qty)

    def _find_context_for_execution_report(self, report: OrderExecutionReport) -> Optional[OrderContext]:
        order_key = self._order_no_index.get(report.broker_order_no)
        if order_key:
            return self._order_states.get(order_key)
        if report.side:
            return self._get_order_context_by_side(report.stock_code, report.side, report.exchange)
        for side in (OrderSide.BUY, OrderSide.SELL):
            context = self._get_order_context_by_side(report.stock_code, side, report.exchange)
            if context and context.broker_order_no == report.broker_order_no:
                return context
        return None

    def _mark_execution_event_seen(self, event_key: str) -> bool:
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

    async def apply_execution_report(self, report: OrderExecutionReport) -> Optional[OrderContext]:
        """체결통보/polling 이벤트를 주문 FSM에 적용합니다."""
        if not report.broker_order_no or not report.stock_code:
            self.logger.warning(f"주문 이벤트 무시: 주문번호/종목코드 누락 - {report.to_dict()}")
            return None

        async with self._get_symbol_lock(report.stock_code, report.exchange):
            context = self._find_context_for_execution_report(report)
            if not context:
                self.logger.warning(
                    f"매칭되는 주문 FSM이 없습니다: 주문번호={report.broker_order_no}, "
                    f"종목={report.stock_code}, 상태={report.event_state.value}"
                )
                return None

            with trace_scope(context.trace_id or ""):
                return await self._apply_execution_report_inner(context, report)

    async def _apply_execution_report_inner(
        self, context: OrderContext, report: OrderExecutionReport
    ) -> Optional[OrderContext]:
        if context.state.is_terminal:
            return context

        if not self._mark_execution_event_seen(report.event_key):
            return context

        if not context.broker_order_no:
            context = self._transition_order_context(
                context.order_key,
                context.state,
                broker_order_no=report.broker_order_no,
            )

        if report.event_state == OrderState.REJECTED:
            return self._transition_order_context(
                context.order_key,
                OrderState.REJECTED,
                error_message=report.message or "주문 거부",
            )

        if report.event_state == OrderState.CANCELED:
            canceled = self._transition_order_context(context.order_key, OrderState.CANCELED)
            return await self._persist_virtual_trade_for_terminal_report(canceled, report)

        if report.fill_qty <= 0:
            if context.state == OrderState.PENDING_SUBMIT:
                return self._transition_order_context(context.order_key, OrderState.SUBMITTED)
            return context

        if report.cumulative_filled_qty is not None:
            filled_qty = max(report.cumulative_filled_qty, context.filled_qty)
        else:
            filled_qty = context.filled_qty + report.fill_qty

        if report.remaining_qty is not None:
            is_filled = report.remaining_qty <= 0
        else:
            is_filled = filled_qty >= context.qty
        if is_filled:
            filled_qty = max(filled_qty, context.qty)
        next_state = OrderState.FILLED if is_filled else OrderState.PARTIAL_FILLED
        quality_update = self._build_execution_quality_update(context, report, filled_qty)
        transitioned = self._transition_order_context(
            context.order_key,
            next_state,
            filled_qty=filled_qty,
            broker_order_no=report.broker_order_no,
            **quality_update,
        )
        self._log_execution_quality(transitioned)
        return await self._persist_virtual_trade_for_terminal_report(transitioned, report)

    async def handle_signing_notice(self, data: dict, tr_id: str = "") -> Optional[OrderContext]:
        """WebSocket signing_notice payload를 정규화한 뒤 FSM에 적용합니다."""
        return await self.apply_execution_report(
            OrderExecutionReport.from_signing_notice(data, tr_id=tr_id)
        )

    def _active_order_contexts(self) -> list[OrderContext]:
        return [
            context
            for context in self._order_states.values()
            if not context.state.is_terminal
        ]

    def _register_post_submit_fast_poll(self, order_key: str, now: Optional[datetime] = None) -> None:
        now = now or (self.market_clock.get_current_kst_time() if self.market_clock else datetime.now())
        self._post_submit_fast_poll_until[order_key] = now + timedelta(seconds=self._POST_SUBMIT_FAST_POLL_WINDOW_SEC)

    def _prune_post_submit_fast_poll(self, now: Optional[datetime] = None) -> None:
        now = now or (self.market_clock.get_current_kst_time() if self.market_clock else datetime.now())
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
        default_interval_sec: int = _DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC,
    ) -> Optional[int]:
        now = now or (self.market_clock.get_current_kst_time() if self.market_clock else datetime.now())
        contexts = self._active_order_contexts()
        if not contexts:
            self._prune_post_submit_fast_poll(now)
            return None

        self._prune_post_submit_fast_poll(now)
        active_order_keys = {context.order_key for context in contexts}
        if any(order_key in self._post_submit_fast_poll_until for order_key in active_order_keys):
            return min(default_interval_sec, self._FAST_POLL_INTERVAL_SEC)
        return default_interval_sec

    def _get_stuck_order_alert_level(
        self,
        context: OrderContext,
        age_sec: float,
    ) -> Optional[NotificationLevel]:
        if context.state not in (OrderState.SUBMITTED, OrderState.PARTIAL_FILLED):
            return None
        if age_sec < self._STUCK_ORDER_WARNING_SEC:
            return None
        if not self._is_paper_trading_mode() and age_sec >= self._STUCK_ORDER_CRITICAL_SEC:
            return NotificationLevel.CRITICAL
        return NotificationLevel.WARNING

    async def check_stuck_orders_once(self, now: Optional[datetime] = None) -> int:
        now = now or self._get_now()
        notified_count = 0

        for context in self._active_order_contexts():
            with trace_scope(context.trace_id or ""):
                entered_at = context.state_entered_at or context.created_at
                if entered_at is None:
                    continue

                age_sec = max((now - entered_at).total_seconds(), 0)
                alert_level = self._get_stuck_order_alert_level(context, age_sec)
                if alert_level is None:
                    continue
                if context.last_stuck_alert_level == alert_level.value:
                    continue

                age_text = f"{age_sec:.0f}s"
                message = (
                    f"stuck order detected: order_key={context.order_key}, "
                    f"broker_order_no={context.broker_order_no or 'N/A'}, "
                    f"side={context.side.value}, qty={context.qty}, "
                    f"filled_qty={context.filled_qty}, remaining_qty={context.remaining_qty}, "
                    f"source={context.source}, state={context.state.value}, age={age_text}"
                )

                if alert_level == NotificationLevel.CRITICAL:
                    self.logger.critical(message)
                else:
                    self.logger.warning(message)

                if self._notification_service:
                    await self._notification_service.emit(
                        NotificationCategory.TRADE,
                        alert_level,
                        "Stuck order detected",
                        message,
                        metadata={
                            "order_key": context.order_key,
                            "broker_order_no": context.broker_order_no or "",
                            "stock_code": context.stock_code,
                            "side": context.side.value,
                            "qty": context.qty,
                            "filled_qty": context.filled_qty,
                            "remaining_qty": context.remaining_qty,
                            "source": context.source,
                            "state": context.state.value,
                            "age_sec": int(age_sec),
                            "trace_id": context.trace_id or "",
                        },
                    )

                if alert_level == NotificationLevel.CRITICAL:
                    today = now.strftime("%Y%m%d")
                    poll_applied = await self._poll_single_order_context(context, today, today)
                    if poll_applied > 0:
                        self.logger.info(
                            f"stuck order 상태 보정 완료(polling): order_key={context.order_key}, "
                            f"applied={poll_applied}"
                        )
                    else:
                        self.logger.warning(
                            f"stuck order polling 결과 모호(상태 전이 없음): "
                            f"order_key={context.order_key}"
                        )

            # polling 이 상태를 terminal 로 전이했을 수 있으므로 재조회 후 갱신
            current_context = self._order_states.get(context.order_key)
            if current_context and not current_context.state.is_terminal:
                self._transition_order_context(
                    context.order_key,
                    current_context.state,
                    stuck_alert_at=now,
                    stuck_alert_level=alert_level.value,
                )
            notified_count += 1

        return notified_count

    @staticmethod
    def _extract_order_query_rows(data) -> list[dict]:
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if not isinstance(data, dict):
            return []

        rows: list[dict] = []
        for key in ("output", "output1", "output2"):
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(row for row in value if isinstance(row, dict))
            elif isinstance(value, dict):
                rows.append(value)
        return rows

    @staticmethod
    def _query_row_matches_context(row: dict, context: OrderContext) -> bool:
        order_no = str(row.get("odno") or row.get("ODNO") or row.get("주문번호") or "").strip()
        stock_code = str(row.get("pdno") or row.get("PDNO") or row.get("종목코드") or "").strip()
        if context.broker_order_no and order_no and order_no != context.broker_order_no:
            return False
        if stock_code and stock_code != context.stock_code:
            return False
        return True

    async def poll_active_orders_once(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """활성 주문을 주문조회 API로 한 번 보정하고 적용한 이벤트 수를 반환합니다."""
        contexts = self._active_order_contexts()
        if not contexts:
            return 0

        if not start_date or not end_date:
            now = self.market_clock.get_current_kst_time() if self.market_clock else datetime.now()
            today = now.strftime("%Y%m%d")
            start_date = start_date or today
            end_date = end_date or today

        applied_count = 0
        for context in contexts:
            with trace_scope(context.trace_id or ""):
                applied_count += await self._poll_single_order_context(context, start_date, end_date)
        return applied_count

    async def _poll_single_order_context(
        self, context: OrderContext, start_date: str, end_date: str
    ) -> int:
        """단일 OrderContext에 대한 polling 처리. trace_scope 내부에서 호출됩니다."""
        applied_count = 0
        side_code = "02" if context.side == OrderSide.BUY else "01"
        response = await self.broker_api_wrapper.inquire_daily_ccld(
            start_date=start_date,
            end_date=end_date,
            side_code=side_code,
            stock_code=context.stock_code,
            ccld_dvsn="00",
            order_no=context.broker_order_no or "",
            exchange=context.exchange,
        )
        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.warning(f"주문조회 polling 실패: {context.order_key} - {msg}")
            return applied_count

        for row in self._extract_order_query_rows(response.data):
            if not self._query_row_matches_context(row, context):
                continue
            report = OrderExecutionReport.from_order_query(row)
            if not report.broker_order_no or not report.stock_code:
                continue
            was_seen = report.event_key in self._processed_execution_events
            before = self._find_context_for_execution_report(report)
            before_snapshot = (
                before.state,
                before.filled_qty,
                before.broker_order_no,
            ) if before else None
            applied = await self.apply_execution_report(report)
            after_snapshot = (
                applied.state,
                applied.filled_qty,
                applied.broker_order_no,
            ) if applied else None
            if applied is not None and not was_seen and before_snapshot != after_snapshot:
                applied_count += 1

        return applied_count

    async def restore_state_from_broker(self) -> int:
        """
        시스템 재시작 시 broker 미체결/체결내역에서 주문 상태를 복원합니다.
        paper mode에서는 스킵합니다.

        반환: 복원된 컨텍스트 수
        """
        if self._is_paper_trading_mode():
            self.logger.info("restore_state_from_broker: 모의투자 모드 — 복원 스킵")
            return 0

        now = self._get_now()
        today = now.strftime("%Y%m%d")
        restored_count = 0

        # 1. 미체결 주문 → SUBMITTED 복원
        unfilled_response = await self.broker_api_wrapper.inquire_unfilled_orders()
        if unfilled_response and unfilled_response.rt_cd == ErrorCode.SUCCESS.value:
            for row in self._extract_order_query_rows(unfilled_response.data):
                odno = str(row.get("odno") or row.get("ODNO") or "").strip()
                pdno = str(row.get("pdno") or row.get("PDNO") or "").strip()
                if not odno or not pdno:
                    continue
                sll_buy = str(row.get("sll_buy_dvsn_cd") or "").strip()
                try:
                    qty = int(row.get("ord_qty") or 0)
                    price = int(float(row.get("ord_unpr") or 0))
                    filled_qty = int(row.get("tot_ccld_qty") or 0)
                except (TypeError, ValueError):
                    continue
                side = OrderSide.BUY if sll_buy == "02" else OrderSide.SELL
                order_key = self._make_order_key(pdno, side, Exchange.KRX)
                if order_key in self._order_states:
                    continue
                context = OrderContext(
                    order_key=order_key,
                    stock_code=pdno,
                    side=side,
                    state=OrderState.SUBMITTED,
                    price=price,
                    qty=qty,
                    exchange=Exchange.KRX,
                    source="restored",
                    filled_qty=filled_qty,
                    broker_order_no=odno,
                    created_at=now,
                    state_entered_at=now,
                )
                self._set_order_context(context)
                restored_count += 1
                self.logger.info(
                    f"restore: 미체결 주문 복원 — stock_code={pdno}, side={side.value}, odno={odno}"
                )
        else:
            self.logger.warning(
                f"restore: 미체결 조회 실패 — "
                f"{unfilled_response.msg1 if unfilled_response else '응답 없음'}"
            )

        # 2. 당일 체결내역 → FILLED 복원 (또는 기존 SUBMITTED → FILLED 전이)
        filled_response = await self.broker_api_wrapper.inquire_filled_history(
            start_date=today, end_date=today
        )
        if filled_response and filled_response.rt_cd == ErrorCode.SUCCESS.value:
            for row in self._extract_order_query_rows(filled_response.data):
                odno = str(row.get("odno") or row.get("ODNO") or "").strip()
                pdno = str(row.get("pdno") or row.get("PDNO") or "").strip()
                if not odno or not pdno:
                    continue
                sll_buy = str(row.get("sll_buy_dvsn_cd") or "").strip()
                try:
                    qty = int(row.get("ord_qty") or 0)
                    price = int(float(row.get("ord_unpr") or 0))
                    filled_qty = int(row.get("tot_ccld_qty") or 0)
                except (TypeError, ValueError):
                    continue
                side = OrderSide.BUY if sll_buy == "02" else OrderSide.SELL
                order_key = self._make_order_key(pdno, side, Exchange.KRX)
                existing = self._order_states.get(order_key)
                if existing:
                    if not existing.state.is_terminal:
                        self._safe_transition_order_context(
                            order_key, OrderState.FILLED, filled_qty=filled_qty
                        )
                    continue
                context = OrderContext(
                    order_key=order_key,
                    stock_code=pdno,
                    side=side,
                    state=OrderState.FILLED,
                    price=price,
                    qty=qty,
                    exchange=Exchange.KRX,
                    source="restored",
                    filled_qty=filled_qty,
                    broker_order_no=odno,
                    created_at=now,
                    state_entered_at=now,
                )
                self._set_order_context(context)
                restored_count += 1
                self.logger.info(
                    f"restore: 당일 체결 복원 — stock_code={pdno}, side={side.value}, odno={odno}"
                )
        else:
            self.logger.warning(
                f"restore: 체결내역 조회 실패 — "
                f"{filled_response.msg1 if filled_response else '응답 없음'}"
            )

        self.logger.info(f"restore_state_from_broker: 총 {restored_count}건 복원 완료")
        return restored_count

    async def reconcile_orders_with_broker(self) -> int:
        """
        활성 주문 상태를 broker 미체결/체결내역/잔고와 비교하여 불일치를 감지합니다.

        정책:
        - 모의투자 모드: 즉시 0 반환 (broker API 호출 없음)
        - 1회 불일치: _reconcile_alarm=True + WARNING, 상태 전이 없음
        - 2회 연속 + 명시 근거(잔고·체결 없음): CANCELED 추정 전이 (assumed=true)
        - 내부 FILLED인데 잔고 없음 → CRITICAL + alarm
        - 잔고에만 있고 내부 컨텍스트 없음 → INFO (외부 주문 가능성)
        - 불일치 0건으로 완료 시: _reconcile_alarm 자동 해제 (일시적 오류 복구)

        반환: 감지된 불일치 건수
        """
        if self._is_paper_trading_mode():
            self.logger.info("reconcile_orders_with_broker: 모의투자 모드 — 스킵")
            return 0

        now = self._get_now()
        today = now.strftime("%Y%m%d")
        mismatch_count = 0
        alarm_triggered_this_run = False

        # --- 1. broker 미체결 주문 조회 ---
        unfilled_response = await self.broker_api_wrapper.inquire_unfilled_orders()
        if not unfilled_response or unfilled_response.rt_cd != ErrorCode.SUCCESS.value:
            self.logger.warning(
                f"reconcile: 미체결 조회 실패 — "
                f"{unfilled_response.msg1 if unfilled_response else '응답 없음'}"
            )
            return 0

        broker_unfilled_order_nos: set[str] = set()
        for row in self._extract_order_query_rows(unfilled_response.data):
            ono = str(row.get("odno") or row.get("ODNO") or "").strip()
            if ono:
                broker_unfilled_order_nos.add(ono)

        # --- 2. broker 당일 체결내역 조회 ---
        filled_response = await self.broker_api_wrapper.inquire_filled_history(
            start_date=today, end_date=today
        )
        if not filled_response or filled_response.rt_cd != ErrorCode.SUCCESS.value:
            self.logger.warning(
                f"reconcile: 체결내역 조회 실패 — "
                f"{filled_response.msg1 if filled_response else '응답 없음'}"
            )
            return 0

        broker_filled: dict[str, int] = {}
        for row in self._extract_order_query_rows(filled_response.data):
            ono = str(row.get("odno") or row.get("ODNO") or "").strip()
            qty_raw = row.get("tot_ccld_qty") or row.get("TOT_CCLD_QTY") or "0"
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                qty = 0
            if ono:
                broker_filled[ono] = max(broker_filled.get(ono, 0), qty)

        # --- 3. 잔고 조회 ---
        balance_response = await self.broker_api_wrapper.get_account_balance()
        if not balance_response or balance_response.rt_cd != ErrorCode.SUCCESS.value:
            self.logger.warning(
                f"reconcile: 잔고 조회 실패 — "
                f"{balance_response.msg1 if balance_response else '응답 없음'}"
            )
            return 0

        broker_balance: dict[str, int] = {}
        raw_holdings = (balance_response.data or {}).get("output1", []) if isinstance(balance_response.data, dict) else []
        for stock in raw_holdings:
            pdno = str(stock.get("pdno") or "").strip()
            qty_raw = stock.get("hldg_qty") or "0"
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                qty = 0
            if pdno and qty > 0:
                broker_balance[pdno] = qty

        # --- 4. 활성 주문과 비교 ---
        active_contexts = self._active_order_contexts()
        seen_stock_codes: set[str] = {c.stock_code for c in active_contexts}

        for context in active_contexts:
            broker_order_no = context.broker_order_no
            if not broker_order_no:
                continue

            in_unfilled = broker_order_no in broker_unfilled_order_nos
            in_filled = broker_order_no in broker_filled

            if in_unfilled or in_filled:
                self._reconcile_consecutive_mismatch_by_key.pop(context.order_key, None)
                continue

            # broker 어디에도 없음 → 불일치 의심
            consecutive = self._reconcile_consecutive_mismatch_by_key.get(context.order_key, 0) + 1
            self._reconcile_consecutive_mismatch_by_key[context.order_key] = consecutive
            self._reconcile_mismatch_count += 1
            mismatch_count += 1

            self.logger.warning(
                f"reconcile mismatch (consecutive={consecutive}): order_key={context.order_key}, "
                f"broker_order_no={broker_order_no}, state={context.state.value} "
                f"— broker 미체결/체결내역 어디에도 없음"
            )
            if not self._reconcile_alarm:
                self._reconcile_alarm = True
                self.logger.warning("reconcile: _reconcile_alarm=True 설정 → 신규 주문 차단")
            alarm_triggered_this_run = True

            if consecutive >= 2:
                filled_from_broker = broker_filled.get(broker_order_no, 0)
                balance_qty = broker_balance.get(context.stock_code, 0)
                if context.side == OrderSide.SELL:
                    clear_evidence = filled_from_broker == 0
                else:
                    clear_evidence = filled_from_broker == 0 and balance_qty == 0

                if clear_evidence:
                    self.logger.warning(
                        f"reconcile: 2회 연속 mismatch + 명시 근거 → CANCELED 추정 전이 "
                        f"(assumed=true): order_key={context.order_key}, "
                        f"broker_order_no={broker_order_no}"
                    )
                    self._safe_transition_order_context(
                        context.order_key,
                        OrderState.CANCELED,
                        error_message="reconcile assumed=true: broker 미체결/체결 근거 없음",
                    )
                    self._reconcile_consecutive_mismatch_by_key.pop(context.order_key, None)

        # --- 5. 내부 FILLED인데 잔고 없음 → CRITICAL ---
        for order_key, context in self._order_states.items():
            if context.state == OrderState.FILLED and context.side == OrderSide.BUY:
                if context.stock_code not in broker_balance:
                    self.logger.critical(
                        f"reconcile: 내부 FILLED이지만 잔고 없음 — "
                        f"order_key={order_key}, stock_code={context.stock_code}, "
                        f"filled_qty={context.filled_qty}"
                    )
                    if not self._reconcile_alarm:
                        self._reconcile_alarm = True
                        self.logger.warning("reconcile: _reconcile_alarm=True 설정 → 신규 주문 차단")
                    alarm_triggered_this_run = True

        # --- 6. 잔고에만 있고 활성 컨텍스트 없음 → INFO ---
        for stock_code, broker_qty in broker_balance.items():
            if stock_code in seen_stock_codes:
                continue
            has_filled = any(
                c.stock_code == stock_code and c.state == OrderState.FILLED
                for c in self._order_states.values()
            )
            if not has_filled:
                self.logger.info(
                    f"reconcile: 잔고에 종목 있지만 내부 활성 컨텍스트 없음 "
                    f"(외부 주문 가능성) — stock_code={stock_code}, broker_qty={broker_qty}"
                )

        # 이번 실행에서 알람 트리거 없음: alarm 자동 해제 (일시적 오류 복구)
        if not alarm_triggered_this_run and self._reconcile_alarm:
            self._reconcile_alarm = False
            self.logger.info("reconcile: 이상 없음 → _reconcile_alarm=False 해제")

        return mismatch_count

    async def resolve_submitted_order(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
        final_state: OrderState = OrderState.FILLED,
        filled_qty: Optional[int] = None,
    ) -> Optional[OrderContext]:
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        order_key = self._make_order_key(stock_code, side, exchange)
        context = self._order_states.get(order_key)
        if not context or context.state.is_terminal:
            return context
        qty = context.qty if filled_qty is None and final_state == OrderState.FILLED else (filled_qty or context.filled_qty)
        return self._transition_order_context(order_key, final_state, filled_qty=qty)

    async def mark_order_partial_filled(
        self,
        stock_code,
        is_buy: bool,
        filled_qty: int,
        *,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return await self.resolve_submitted_order(
            stock_code,
            is_buy,
            exchange=exchange,
            final_state=OrderState.PARTIAL_FILLED,
            filled_qty=filled_qty,
        )

    async def mark_order_canceled(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return await self.resolve_submitted_order(
            stock_code,
            is_buy,
            exchange=exchange,
            final_state=OrderState.CANCELED,
        )

    async def cancel_order(
        self,
        stock_code: Optional[str] = None,
        is_buy: Optional[bool] = None,
        *,
        broker_order_no: Optional[str] = None,
        exchange: Exchange = Exchange.KRX,
        order_orgno: str = "06010",
    ) -> ResCommonResponse:
        context = None
        if broker_order_no:
            order_key = self._order_no_index.get(broker_order_no)
            context = self._order_states.get(order_key) if order_key else None
        if context is None and stock_code is not None and is_buy is not None:
            context = self.get_order_context(stock_code, is_buy, exchange)

        if context is None:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="취소할 활성 주문 컨텍스트를 찾을 수 없습니다.",
                data=None,
            )
        if context.state.is_terminal:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=f"이미 종료된 주문은 취소할 수 없습니다. 상태={context.state.value}",
                data=context.to_dict(),
            )

        target_order_no = broker_order_no or context.broker_order_no
        if not target_order_no:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="broker_order_no가 없어 취소 요청을 보낼 수 없습니다.",
                data=context.to_dict(),
            )
        if context.broker_order_no and target_order_no != context.broker_order_no:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="요청한 broker_order_no가 로컬 주문 컨텍스트와 일치하지 않습니다.",
                data=context.to_dict(),
            )

        remaining_qty = max(context.remaining_qty, 0)
        if remaining_qty <= 0:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="취소 가능한 잔여 수량이 없습니다.",
                data=context.to_dict(),
            )

        try:
            result = await self.broker_api_wrapper.cancel_stock_order(
                broker_order_no=target_order_no,
                order_qty=remaining_qty,
                order_price=0,
                order_orgno=order_orgno,
                order_dvsn="00",
                qty_all_ord_yn="Y",
                exchange=context.exchange,
            )
        except Exception as e:
            self.logger.exception(f"주문 취소 요청 중 오류 발생: {str(e)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"주문 취소 요청 중 예외 발생: {str(e)}",
                data=context.to_dict(),
            )

        if result and result.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(
                f"주문 취소 요청 성공: order_key={context.order_key}, broker_order_no={target_order_no}"
            )
        else:
            message = result.msg1 if result else "응답 없음"
            self.logger.warning(
                f"주문 취소 요청 실패: order_key={context.order_key}, broker_order_no={target_order_no}, 사유={message}"
            )
        return result

    async def mark_order_rejected(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
        error_message: str = "",
    ) -> Optional[OrderContext]:
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        order_key = self._make_order_key(stock_code, side, exchange)
        context = self._order_states.get(order_key)
        if not context or context.state.is_terminal:
            return context
        return self._transition_order_context(
            order_key,
            OrderState.REJECTED,
            error_message=error_message,
        )

    async def _retry_order(self, order_fn, stock_code, price, qty, order_key: Optional[str] = None) -> ResCommonResponse:
        """재시도 가능한 오류에 대해 주문 API를 재시도.
        - FAIL (비즈니스 거부): 즉시 REJECTED, 재시도 없음.
        - RETRY (일시적 오류): 지수 백오프, 최대 _ORDER_MAX_RETRIES 회.
        """
        last_result = None
        for attempt in range(1, self._ORDER_MAX_RETRIES + 1):
            result: ResCommonResponse = await order_fn(stock_code, price, qty)
            if result and result.rt_cd == ErrorCode.SUCCESS.value:
                if order_key and order_key in self._order_states:
                    self._transition_order_context(
                        order_key,
                        OrderState.SUBMITTED,
                        attempt_count=attempt,
                        broker_order_no=self._extract_broker_order_no(result),
                    )
                return result
            last_result = result

            outcome = classify(result)

            if outcome == RequestOutcome.FAIL:
                # 비즈니스 거부(잔고부족, 종목코드오류 등) → 즉시 REJECTED, 재시도 없음
                self.logger.warning(
                    f"주문 비즈니스 거부 (재시도 없음) — {stock_code}, "
                    f"사유: {result.msg1 if result else '응답 없음'}"
                )
                if order_key and order_key in self._order_states:
                    self._transition_order_context(
                        order_key,
                        OrderState.REJECTED,
                        attempt_count=attempt,
                        error_code=result.rt_cd if result else None,
                        error_message=result.msg1 if result else "응답 없음",
                    )
                break

            # RETRY (일시적 오류)
            if order_key and order_key in self._order_states:
                current_state = OrderState.PENDING_SUBMIT if attempt < self._ORDER_MAX_RETRIES else OrderState.REJECTED
                self._transition_order_context(
                    order_key,
                    current_state,
                    attempt_count=attempt,
                    error_code=result.rt_cd if result else None,
                    error_message=result.msg1 if result else "응답 없음",
                )

            if attempt < self._ORDER_MAX_RETRIES:
                self.logger.warning(
                    f"주문 재시도 {attempt}/{self._ORDER_MAX_RETRIES}: "
                    f"{stock_code}, 사유: {result.msg1 if result else '응답 없음'}"
                )
                await self.market_clock.async_sleep(self._ORDER_RETRY_DELAY_SEC * attempt)
                continue
            break
        return last_result

    async def _execute_order_via_broker(self, stock_code, price, qty, is_buy: bool, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        action_str = "매수" if is_buy else "매도"
        self.logger.info(f"OrderExecutionService - 주식 {action_str} 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        try:
            result = await self.broker_api_wrapper.place_stock_order(stock_code, price, qty, is_buy=is_buy, exchange=exchange)
            if self._kill_switch:
                if result and result.rt_cd == ErrorCode.SUCCESS.value:
                    await self._kill_switch.record_api_success()
                else:
                    rt = result.rt_cd if result else "no_response"
                    await self._kill_switch.record_api_failure(rt)
            return result
        except Exception as e:
            self.logger.exception(f"{action_str} 주문 중 오류 발생: {str(e)}")
            if self._kill_switch:
                await self._kill_switch.record_api_failure(str(e))
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"{action_str} 주문 처리 중 예외 발생: {str(e)}", data=None)

    async def _submit_order_with_fsm(
        self,
        *,
        stock_code,
        price,
        qty,
        exchange: Exchange,
        side: OrderSide,
        source: str,
        finalize_immediately: bool,
        trace_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ) -> ResCommonResponse:
        order_key = self._make_order_key(stock_code, side, exchange)
        action_kr = "매수" if side == OrderSide.BUY else "매도"
        resolved_intent_id = intent_id or str(uuid.uuid4())

        if self._reconcile_alarm:
            self.logger.warning(
                f"reconcile alarm 활성 → 신규 주문 차단: stock_code={stock_code}, side={side.value}"
            )
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="reconcile alarm 활성: 불일치 감지로 신규 주문이 차단되었습니다.",
                data=None,
            )

        async with self._get_symbol_lock(stock_code, exchange):
            # 중복 intent 차단: 동일 intent_id + 활성 상태이면 즉시 거부
            existing_key_for_intent = self._intent_index.get(resolved_intent_id)
            if existing_key_for_intent:
                existing_ctx = self._order_states.get(existing_key_for_intent)
                if existing_ctx and not existing_ctx.state.is_terminal:
                    self.logger.warning(
                        f"중복 intent 차단: intent_id={resolved_intent_id}, "
                        f"order_key={existing_key_for_intent}, state={existing_ctx.state.value}"
                    )
                    return ResCommonResponse(
                        rt_cd=ErrorCode.API_ERROR.value,
                        msg1=f"duplicate intent: 이미 처리 중인 주문 요청입니다. intent_id={resolved_intent_id}",
                        data=existing_ctx.to_dict(),
                    )

            for existing_side in (OrderSide.BUY, OrderSide.SELL):
                existing = self._get_order_context_by_side(stock_code, existing_side, exchange)
                if existing and not existing.state.is_terminal:
                    self.logger.warning(
                        f"{action_kr} 주문 차단: 진행 중인 주문이 있습니다. "
                        f"종목={stock_code}, 기존상태={existing.state.value}, 기존주문={existing.order_key}"
                    )
                    return ResCommonResponse(
                        rt_cd=ErrorCode.RETRY_LIMIT.value,
                        msg1=f"진행 중인 주문이 있어 {action_kr} 주문을 차단했습니다. 상태={existing.state.value}",
                        data=existing.to_dict(),
                    )

            policy_decision = None
            if self._order_policy is not None:
                policy_decision = await self._order_policy.validate_order(
                    stock_code=stock_code,
                    price=price,
                    qty=qty,
                    side=side,
                    exchange=exchange,
                )
                if policy_decision.blocked:
                    return policy_decision.to_response()
                if (
                    policy_decision.adjusted_price is not None
                    and policy_decision.adjusted_price != price
                ):
                    self.logger.info(
                        f"OrderPolicy 가격 조정: 종목={stock_code}, "
                        f"{price} -> {policy_decision.adjusted_price}, "
                        f"rule={policy_decision.rule}"
                    )
                    price = policy_decision.adjusted_price

            if self._risk_gate is not None:
                strategy_name, is_strategy_source = self._strategy_name_from_source(source)
                blocked = await self._risk_gate.validate_order(
                    stock_code=stock_code,
                    price=price,
                    qty=qty,
                    side=side,
                    exchange=exchange,
                    active_order_count=len(self._active_order_contexts()),
                    source=source,
                    strategy_name=strategy_name if is_strategy_source else None,
                )
                if blocked is not None:
                    return blocked

            context = OrderContext(
                order_key=order_key,
                stock_code=stock_code,
                side=side,
                state=OrderState.PENDING_SUBMIT,
                exchange=exchange,
                price=price,
                qty=qty,
                source=source,
                trace_id=trace_id,
                intent_id=resolved_intent_id,
                expected_fill_price=self._resolve_expected_fill_price(price, policy_decision),
            )
            self._set_order_context(context)
            self._intent_index[resolved_intent_id] = order_key

            self._log_real_order_preview(
                stock_code=stock_code,
                side=side,
                price=price,
                qty=qty,
                exchange=exchange,
                source=source,
                trace_id=trace_id,
                intent_id=resolved_intent_id,
                order_key=order_key,
            )

            result = await self._retry_order(
                lambda c, p, q: self._execute_order_via_broker(
                    c, p, q, is_buy=(side == OrderSide.BUY), exchange=exchange
                ),
                stock_code,
                price,
                qty,
                order_key=order_key,
            )

            latest = self._order_states.get(order_key)
            if result and result.rt_cd == ErrorCode.SUCCESS.value:
                if latest and latest.state == OrderState.SUBMITTED:
                    self._register_post_submit_fast_poll(order_key)
                if self._resolve_finalize(finalize_immediately) and latest and latest.state == OrderState.SUBMITTED:
                    self._transition_order_context(order_key, OrderState.FILLED, filled_qty=qty)
                return result

            if latest and latest.state == OrderState.PENDING_SUBMIT:
                self._transition_order_context(
                    order_key,
                    OrderState.REJECTED,
                    error_code=result.rt_cd if result else None,
                    error_message=result.msg1 if result else "응답 없음",
                )
            return result

    async def handle_place_buy_order(
        self,
        stock_code,
        price,
        qty,
        exchange: Exchange = Exchange.KRX,
        source: str = "default",
        finalize_immediately: bool = True,
        *,
        trace_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ):
        """주식 매수 주문 요청 및 결과 출력."""
        current_trace = trace_id or get_trace_id() or new_trace_id("MANUAL")
        with trace_scope(current_trace):
            t_start = self.pm.start_timer()
            if self.market_calendar_service and not await self.market_calendar_service.is_market_open_now():
                self.logger.warning("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
                return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)
            # Fallback if market_calendar_service is not available (though it should be)
            elif not self.market_calendar_service and not self.market_clock.is_market_operating_hours():
                return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)

            buy_order_result: ResCommonResponse = await self._submit_order_with_fsm(
                stock_code=stock_code,
                price=price,
                qty=qty,
                exchange=exchange,
                side=OrderSide.BUY,
                source=source,
                finalize_immediately=finalize_immediately,
                trace_id=current_trace,
                intent_id=intent_id,
            )
            if buy_order_result and buy_order_result.rt_cd == ErrorCode.SUCCESS.value:
                self.logger.info(
                    f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{buy_order_result.rt_cd}', 'msg1': '{buy_order_result.msg1}'}}")
                if self._price_sub_svc:
                    asyncio.create_task(self._price_sub_svc.add_subscription(
                        stock_code, SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE
                    ))
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.API, NotificationLevel.INFO, "매수 주문 성공",
                                        f"{stock_code} {qty}주 @ {price}원",
                                        metadata={"code": stock_code, "qty": qty, "price": price, "trace_id": current_trace})
            else:
                rt_cd = buy_order_result.rt_cd if buy_order_result else 'None'
                msg1 = buy_order_result.msg1 if buy_order_result else '응답 없음'
                self.logger.error(
                    f"주식 매수 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{rt_cd}', 'msg1': '{msg1}'}}")
                if self._virtual_trade_service:
                    await self._virtual_trade_service.log_order_failure_async("BUY", stock_code, price, qty, msg1)
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "매수 주문 실패",
                                        f"{stock_code} - {msg1}",
                                        metadata={"code": stock_code, "error": msg1, "trace_id": current_trace})
            self.pm.log_timer(f"OrderExecutionService.handle_place_buy_order({stock_code})", t_start)
            return buy_order_result

    async def handle_place_sell_order(
        self,
        stock_code,
        price,
        qty,
        exchange: Exchange = Exchange.KRX,
        source: str = "default",
        finalize_immediately: bool = True,
        *,
        trace_id: Optional[str] = None,
        intent_id: Optional[str] = None,
    ):
        """주식 매도 주문 요청 및 결과 출력."""
        current_trace = trace_id or get_trace_id() or new_trace_id("MANUAL")
        with trace_scope(current_trace):
            t_start = self.pm.start_timer()
            if self.market_calendar_service and not await self.market_calendar_service.is_market_open_now():
                self.logger.warning("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
                return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)
            # Fallback if market_calendar_service is not available
            elif not self.market_calendar_service and not self.market_clock.is_market_operating_hours():
                return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)

            sell_order_result: ResCommonResponse = await self._submit_order_with_fsm(
                stock_code=stock_code,
                price=price,
                qty=qty,
                exchange=exchange,
                side=OrderSide.SELL,
                source=source,
                finalize_immediately=finalize_immediately,
                trace_id=current_trace,
                intent_id=intent_id,
            )
            if sell_order_result and sell_order_result.rt_cd == ErrorCode.SUCCESS.value:
                self.logger.info(
                    f"주식 매도 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{sell_order_result.rt_cd}', 'msg1': '{sell_order_result.msg1}'}}")
                if self._price_sub_svc:
                    asyncio.create_task(self._price_sub_svc.remove_subscription(
                        stock_code, "portfolio"
                    ))
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.API, NotificationLevel.INFO, "매도 주문 성공",
                                        f"{stock_code} {qty}주 @ {price}원",
                                        metadata={"code": stock_code, "qty": qty, "price": price, "trace_id": current_trace})
            else:
                rt_cd = sell_order_result.rt_cd if sell_order_result else 'None'
                msg1 = sell_order_result.msg1 if sell_order_result else '응답 없음'
                self.logger.error(
                    f"주식 매도 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{rt_cd}', 'msg1': '{msg1}'}}")
                if self._virtual_trade_service:
                    await self._virtual_trade_service.log_order_failure_async("SELL", stock_code, price, qty, msg1)
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "매도 주문 실패",
                                        f"{stock_code} - {msg1}",
                                        metadata={"code": stock_code, "error": msg1, "trace_id": current_trace})
            self.pm.log_timer(f"OrderExecutionService.handle_place_sell_order({stock_code})", t_start)
            return sell_order_result

    async def handle_buy_stock(
        self,
        stock_code,
        qty_input,
        price_input,
        exchange: Exchange = Exchange.KRX,
        source: str = "manual:수동매매",
        finalize_immediately: bool = True,
    ):
        """
        사용자 입력을 받아 주식 매수 주문을 처리합니다.
        trading_app.py의 '3'번 옵션에 매핑됩니다.
        """

        try:
            qty = int(qty_input)
            price = int(price_input)
        except ValueError:
            msg = f"잘못된 매수 입력: 수량={qty_input}, 가격={price_input}"
            self.logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)

        # handle_place_buy_order 호출
        return await self.handle_place_buy_order(
            stock_code,
            price,
            qty,
            exchange=exchange,
            source=source,
            finalize_immediately=finalize_immediately,
        )

    async def handle_sell_stock(
        self,
        stock_code,
        qty_input,
        price_input,
        exchange: Exchange = Exchange.KRX,
        source: str = "manual:수동매매",
        finalize_immediately: bool = True,
    ):
        """
        사용자 입력을 받아 주식 매도 주문을 처리합니다.
        trading_app.py의 '4'번 옵션에 매핑됩니다.
        """
        try:
            qty = int(qty_input)
            price = int(price_input)
        except ValueError:
            msg = f"잘못된 매도 입력: 수량={qty_input}, 가격={price_input}"
            self.logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)

        # handle_place_sell_order 호출
        return await self.handle_place_sell_order(
            stock_code,
            price,
            qty,
            exchange=exchange,
            source=source,
            finalize_immediately=finalize_immediately,
        )

    async def sell_all_stocks(self, exchange: Exchange = Exchange.KRX):
        """보유하고 있는 모든 주식을 시장가로 매도합니다."""
        self.logger.info("모든 보유 주식의 일괄 매도를 시작합니다.")
        t_start = self.pm.start_timer()
        if self.market_calendar_service and not await self.market_calendar_service.is_market_open_now():
            self.logger.warning("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)
        # Fallback if market_calendar_service is not available
        elif not self.market_calendar_service and not self.market_clock.is_market_operating_hours():
            return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)

        try:
            # 1. 보유 주식 목록 조회
            balance_response = await self.broker_api_wrapper.get_account_balance()
            if balance_response.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"잔고 조회 실패: {balance_response.msg1}")
                return {"error": f"잔고 조회에 실패했습니다: {balance_response.msg1}"}

            holdings = balance_response.data.get('output1', [])
            if not holdings:
                self.logger.info("매도할 보유 주식이 없습니다.")
                return {"message": "보유 중인 주식이 없습니다.", "results": []}

            # 2. 각 주식에 대해 매도 주문 실행
            sell_tasks = []
            for stock in holdings:
                stock_code = stock.get('pdno')
                quantity = int(stock.get('hldg_qty', 0))
                
                if stock_code and quantity > 0:
                    # 시장가 주문을 위해 가격을 0으로 설정
                    task = self.handle_place_sell_order(stock_code, 0, quantity, exchange=exchange)
                    sell_tasks.append((stock_code, task))

            if not sell_tasks:
                self.logger.info("매도할 유효한 주식이 없습니다.")
                return {"message": "매도할 유효한 주식이 없습니다.", "results": []}

            # 3. 매도 주문 결과 집계
            results = []
            for stock_code, task in sell_tasks:
                try:
                    result = await task
                    if result and result.rt_cd == ErrorCode.SUCCESS.value:
                        self.logger.info(f"매도 주문 성공: {stock_code}")
                        results.append({"stock_code": stock_code, "success": True, "message": result.msg1})
                    else:
                        msg = result.msg1 if result else "알 수 없는 오류"
                        self.logger.error(f"매도 주문 실패: {stock_code}, 이유: {msg}")
                        results.append({"stock_code": stock_code, "success": False, "message": msg})
                except Exception as e:
                    self.logger.error(f"매도 주문 중 예외 발생: {stock_code}, 오류: {str(e)}")
                    results.append({"stock_code": stock_code, "success": False, "message": str(e)})

            self.logger.info("일괄 매도 절차가 완료되었습니다.")
            self.pm.log_timer(f"OrderExecutionService.sell_all_stocks({stock_code})", t_start)
            return {"message": "일괄 매도가 완료되었습니다.", "results": results}

        except Exception as e:
            self.logger.critical(f"일괄 매도 중 심각한 오류 발생: {e}", exc_info=True)
            return {"error": f"일괄 매도 중 심각한 오류가 발생했습니다: {str(e)}"}

    async def handle_realtime_price_quote_stream(self, stock_code):
        """
        실시간 주식 체결가/호가 스트림을 시작하고,
        사용자 입력이 있을 때까지 데이터를 수신합니다.
        """
        self.logger.info(f"\n--- 실시간 주식 체결가/호가 구독 시작 ({stock_code}) ---")

        # 콜백 함수 정의
        def realtime_data_display_callback(data):
            if isinstance(data, dict):
                data_type = data.get('type')
                output = data.get('data', {})

                if data_type == 'realtime_price':  # 주식 체결
                    current_price = output.get('STCK_PRPR', 'N/A')
                    acml_vol = output.get('ACML_VOL', 'N/A')
                    trade_time = output.get('STCK_CNTG_HOUR', 'N/A')
                    change_val = output.get('PRDY_VRSS', 'N/A')
                    change_sign = output.get('PRDY_VRSS_SIGN', 'N/A')
                    change_rate = output.get('PRDY_CTRT', 'N/A')

                    display_message = (
                        f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                        f"전일대비: {change_sign}{change_val} ({change_rate}%), 누적량: {acml_vol}"
                    )
                    self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'realtime_quote':  # 주식 호가
                    askp1 = output.get('매도호가1', 'N/A')
                    bidp1 = output.get('매수호가1', 'N/A')
                    trade_time = output.get('영업시간', 'N/A')
                    display_message = (
                        f"\r[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1: {askp1}, 매수1: {bidp1}{' ' * 20}"
                    )
                    self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'signing_notice':  # 체결 통보
                    order_num = output.get('주문번호', 'N/A')
                    trade_qty = output.get('체결수량', 'N/A')
                    trade_price = output.get('체결단가', 'N/A')
                    trade_time = output.get('주식체결시간', 'N/A')
                    self.logger.debug(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")
                else:
                    self.logger.debug(f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}")

        # 웹소켓 연결 및 구독 요청
        if await self.broker_api_wrapper.connect_websocket(on_message_callback=realtime_data_display_callback):
            await self.broker_api_wrapper.subscribe_realtime_price(stock_code)
            await self.broker_api_wrapper.subscribe_realtime_quote(stock_code)

            try:
                await asyncio.to_thread(input)

            except KeyboardInterrupt:
                self.logger.info("실시간 구독 중단 (KeyboardInterrupt).")
            finally:
                await self.broker_api_wrapper.unsubscribe_realtime_price(stock_code)
                await self.broker_api_wrapper.unsubscribe_realtime_quote(stock_code)
                await self.broker_api_wrapper.disconnect_websocket()
                self.logger.info(f"실시간 주식 스트림 종료: 종목={stock_code}")
        else:
            self.logger.error("실시간 웹소켓 연결 실패.")
