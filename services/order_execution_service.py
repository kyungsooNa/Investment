# app/order_execution_service.py
import asyncio
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Optional
from common.types import ErrorCode, ResCommonResponse, Exchange, OrderContext, OrderSide, OrderState, OrderExecutionReport
from core.api_priority import emergency_scope
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
from config.config_loader import ExecutionQualityReportConfig
from services.execution_quality_reporter import ExecutionQualityReporter
from services.broker_order_submitter import BrokerOrderSubmitter
from services.order_state_machine import OrderStateMachine
from services.fill_reconciliation_service import FillReconciliationService
from services.order_submission_coordinator import OrderSubmissionCoordinator


class ClearanceMode(str, Enum):
    """sell_all_stocks 운영 목적별 청산 모드.

    - SAFE_SEQUENTIAL: 기본값. 순차 매도 (수동 전체매도 등 일반 경로).
    - BOUNDED_PARALLEL: Semaphore 기반 제한 병렬 청산.
    - EMERGENCY: 전체 동시 청산 (킬스위치/장애 등 빠른 위험 축소).
    """

    SAFE_SEQUENTIAL = "safe_sequential"
    BOUNDED_PARALLEL = "bounded_parallel"
    EMERGENCY = "emergency"


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
                 order_policy_service: Optional[OrderPolicyService] = None,
                 data_quality_service=None,
                 execution_quality_config: Optional[ExecutionQualityReportConfig] = None,
                 deferred_order_queue=None,
                 order_max_retries: int = _ORDER_MAX_RETRIES,
                 order_retry_delay_sec: int = _ORDER_RETRY_DELAY_SEC):
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
        self._data_quality = data_quality_service
        self._execution_quality_config = execution_quality_config or ExecutionQualityReportConfig()
        self._order_max_retries = self._validate_order_max_retries(order_max_retries)
        self._order_retry_delay_sec = self._validate_order_retry_delay_sec(order_retry_delay_sec)
        self._exec_quality_reporter = ExecutionQualityReporter(
            logger=logger,
            config=self._execution_quality_config,
            notification_service=notification_service,
            now_provider=self._get_now,
        )
        self._deferred_queue = deferred_order_queue
        self._notification_tasks: set[asyncio.Task] = set()
        self._fsm = OrderStateMachine(
            logger=logger,
            now_provider=self._get_now,
            deferred_queue=self._deferred_queue,
            notification_tasks=self._notification_tasks,
            processed_execution_event_limit=self._PROCESSED_EXECUTION_EVENT_LIMIT,
            post_submit_fast_poll_window_sec=self._POST_SUBMIT_FAST_POLL_WINDOW_SEC,
            fast_poll_interval_sec=self._FAST_POLL_INTERVAL_SEC,
            default_active_poll_interval_sec=self._DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC,
        )
        self._fill_reconciliation = FillReconciliationService(
            broker_api_wrapper=broker_api_wrapper,
            logger=logger,
            state_machine=self._fsm,
            execution_quality_reporter=self._exec_quality_reporter,
            virtual_trade_service=virtual_trade_service,
            kill_switch_service=kill_switch_service,
            account_snapshot_cache=account_snapshot_cache,
            market_clock=market_clock,
            notification_service=notification_service,
            now_provider=self._get_now,
            is_paper_trading_fn=self._is_paper_trading_mode,
            stuck_order_warning_sec=self._STUCK_ORDER_WARNING_SEC,
            stuck_order_critical_sec=self._STUCK_ORDER_CRITICAL_SEC,
        )
        self._broker_submitter = BrokerOrderSubmitter(
            broker_api_wrapper=broker_api_wrapper,
            logger=logger,
            kill_switch=kill_switch_service,
            market_clock=market_clock,
            state_provider=lambda: self._fsm._order_states,
            transition_fn=self._fsm.transition,
            extract_broker_order_no_fn=OrderStateMachine.extract_broker_order_no,
            on_missing_broker_order_no_fn=self._fill_reconciliation.on_broker_order_no_missing,
            max_retries=self._order_max_retries,
            retry_delay_sec=self._order_retry_delay_sec,
        )
        self._fsm.set_on_critical_mismatch(
            self._fill_reconciliation.on_safe_transition_critical
        )
        self._submission_coordinator = OrderSubmissionCoordinator(
            logger=logger,
            broker_api_wrapper=broker_api_wrapper,
            state_machine=self._fsm,
            broker_submitter=self._broker_submitter,
            execution_quality_reporter=self._exec_quality_reporter,
            fill_reconciliation_service=self._fill_reconciliation,
            deferred_order_queue=self._deferred_queue,
            data_quality_service=data_quality_service,
            order_policy_service=order_policy_service,
            risk_gate_service=risk_gate_service,
            notification_service=notification_service,
            is_paper_trading_fn=self._is_paper_trading_mode,
        )

    @staticmethod
    def _validate_order_max_retries(value: int) -> int:
        retries = int(value)
        if retries < 1:
            raise ValueError("order_max_retries must be >= 1")
        return retries

    @staticmethod
    def _validate_order_retry_delay_sec(value: int) -> int:
        delay = int(value)
        if delay < 0:
            raise ValueError("order_retry_delay_sec must be >= 0")
        return delay

    # ── FSM state property delegations (Phase 3 백워드 호환) ────────────
    @property
    def _order_states(self):
        return self._fsm._order_states

    @property
    def _order_locks(self):
        return self._fsm._order_locks

    @property
    def _order_no_index(self):
        return self._fsm._order_no_index

    @property
    def _intent_index(self):
        return self._fsm._intent_index

    @property
    def _processed_execution_events(self):
        return self._fsm._processed_execution_events

    @property
    def _processed_execution_event_limit(self):
        return self._fsm._processed_execution_event_limit

    @_processed_execution_event_limit.setter
    def _processed_execution_event_limit(self, value):
        self._fsm._processed_execution_event_limit = value

    @property
    def _post_submit_fast_poll_until(self):
        return self._fsm._post_submit_fast_poll_until

    @property
    def _reconcile_mismatch_count(self):
        return self._fsm._reconcile_mismatch_count

    @_reconcile_mismatch_count.setter
    def _reconcile_mismatch_count(self, value):
        self._fsm._reconcile_mismatch_count = value

    # ── FillReconciliationService 소유 상태 delegation (Phase 4) ──
    @property
    def _reconcile_alarm(self):
        return self._fill_reconciliation._reconcile_alarm

    @_reconcile_alarm.setter
    def _reconcile_alarm(self, value):
        self._fill_reconciliation._reconcile_alarm = value

    @property
    def _reconcile_consecutive_mismatch_by_key(self):
        return self._fill_reconciliation._reconcile_consecutive_mismatch_by_key

    def _get_now(self) -> datetime:
        return self.market_clock.get_current_kst_time() if self.market_clock else datetime.now()

    def _is_paper_trading_mode(self) -> bool:
        env = getattr(self.broker_api_wrapper, "env", None)
        return getattr(env, "is_paper_trading", True)

    @staticmethod
    def _is_strategy_source(source: str) -> bool:
        text = str(source or "")
        return text.startswith("strategy:") or text.startswith("strategy_force_exit:")

    def _log_real_order_preview(self, **kwargs) -> None:
        return self._submission_coordinator._log_real_order_preview(**kwargs)

    def _resolve_finalize(self, requested: bool) -> bool:
        return self._submission_coordinator._resolve_finalize(requested)

    def _make_order_key(self, stock_code: str, side: OrderSide, exchange: Exchange) -> str:
        return OrderStateMachine.make_order_key(stock_code, side, exchange)

    def _make_symbol_lock_key(self, stock_code: str, exchange: Exchange) -> str:
        return OrderStateMachine.make_symbol_lock_key(stock_code, exchange)

    def _get_symbol_lock(self, stock_code: str, exchange: Exchange) -> asyncio.Lock:
        return self._fsm.symbol_lock(stock_code, exchange)

    def _get_order_context_by_side(
        self,
        stock_code: str,
        side: OrderSide,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return self._fsm.lookup_by_side(stock_code, side, exchange)

    def get_order_context(self, stock_code, is_buy: bool, exchange: Exchange = Exchange.KRX) -> Optional[OrderContext]:
        return self._fsm.lookup_by_buy_flag(stock_code, is_buy, exchange)

    def has_active_order(self, stock_code, exchange: Exchange = Exchange.KRX) -> bool:
        return self._fsm.has_active(stock_code, exchange)

    def _extract_broker_order_no(self, result: ResCommonResponse) -> Optional[str]:
        return OrderStateMachine.extract_broker_order_no(result)

    def _set_order_context(self, context: OrderContext) -> OrderContext:
        return self._fsm.register(context)

    def _transition_order_context(self, order_key: str, new_state: OrderState, **kwargs) -> OrderContext:
        return self._fsm.transition(order_key, new_state, **kwargs)

    def _schedule_deferred_release(self, stock_code: str) -> None:
        self._fsm._schedule_deferred_release(stock_code)

    def _safe_transition_order_context(self, order_key: str, new_state: OrderState, **kwargs) -> Optional[OrderContext]:
        return self._fsm.safe_transition(order_key, new_state, **kwargs)

    def _mark_virtual_trade_recorded(self, context: OrderContext, recorded_qty: int) -> OrderContext:
        return self._fsm.mark_virtual_trade_recorded(context, recorded_qty)

    @staticmethod
    def _strategy_name_from_source(source: str) -> tuple[str, bool]:
        source = source or ""
        if source.startswith("strategy:"):
            return source.split(":", 1)[1] or "default", True
        if source.startswith("strategy_force_exit:"):
            return source.split(":", 1)[1] or "default", True
        if source.startswith("manual:"):
            return source.split(":", 1)[1] or "수동매매", False
        if source in ("", "default", "manual", "web"):
            return "수동매매", False
        return source, False

    @staticmethod
    def _is_reconcile_source(source: str) -> bool:
        return (source or "").startswith("reconcile:")

    async def _persist_virtual_trade_for_terminal_report(self, context, report):
        return await self._fill_reconciliation._persist_virtual_trade_for_terminal_report(context, report)

    def _find_context_for_execution_report(self, report: OrderExecutionReport) -> Optional[OrderContext]:
        return self._fsm.find_context_for_execution_report(report)

    def _mark_execution_event_seen(self, event_key: str) -> bool:
        return self._fsm.mark_execution_event_seen(event_key)

    async def apply_execution_report(self, report: OrderExecutionReport) -> Optional[OrderContext]:
        return await self._fill_reconciliation.apply_execution_report(report)

    async def _apply_execution_report_inner(self, context, report):
        return await self._fill_reconciliation._apply_execution_report_inner(context, report)

    async def handle_signing_notice(self, data: dict, tr_id: str = "") -> Optional[OrderContext]:
        return await self._fill_reconciliation.handle_signing_notice(data, tr_id)

    def _active_order_contexts(self) -> list[OrderContext]:
        return self._fsm.active_contexts()

    def get_active_order_summary(self) -> dict:
        summary = self._fsm.active_summary()
        summary["reconcile_alarm"] = self._reconcile_alarm
        return summary

    def _register_post_submit_fast_poll(self, order_key: str, now: Optional[datetime] = None) -> None:
        self._fsm.register_post_submit_fast_poll(order_key, now)

    def _prune_post_submit_fast_poll(self, now: Optional[datetime] = None) -> None:
        self._fsm.prune_post_submit_fast_poll(now)

    def get_active_order_poll_interval_sec(
        self,
        now: Optional[datetime] = None,
        *,
        default_interval_sec: int = _DEFAULT_ACTIVE_ORDER_POLL_INTERVAL_SEC,
    ) -> Optional[int]:
        return self._fsm.get_active_order_poll_interval_sec(
            now, default_interval_sec=default_interval_sec
        )

    def _get_stuck_order_alert_level(self, context, age_sec):
        return self._fill_reconciliation._get_stuck_order_alert_level(context, age_sec)

    async def check_stuck_orders_once(self, now: Optional[datetime] = None) -> int:
        return await self._fill_reconciliation.check_stuck_orders_once(now)

    @staticmethod
    def _extract_order_query_rows(data) -> list[dict]:
        return FillReconciliationService._extract_order_query_rows(data)

    @staticmethod
    def _query_row_matches_context(row: dict, context: OrderContext) -> bool:
        return FillReconciliationService._query_row_matches_context(row, context)

    async def poll_active_orders_once(self, *, start_date=None, end_date=None) -> int:
        return await self._fill_reconciliation.poll_active_orders_once(start_date=start_date, end_date=end_date)

    async def _poll_single_order_context(self, context, start_date, end_date) -> int:
        return await self._fill_reconciliation._poll_single_order_context(context, start_date, end_date)

    async def restore_state_from_broker(self) -> int:
        return await self._fill_reconciliation.restore_state_from_broker()

    async def reconcile_orders_with_broker(self) -> int:
        return await self._fill_reconciliation.reconcile_orders_with_broker()

    async def resolve_submitted_order(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
        final_state: OrderState = OrderState.FILLED,
        filled_qty: Optional[int] = None,
    ) -> Optional[OrderContext]:
        return await self._fill_reconciliation.resolve_submitted_order(
            stock_code, is_buy, exchange=exchange, final_state=final_state, filled_qty=filled_qty,
        )

    async def mark_order_partial_filled(
        self,
        stock_code,
        is_buy: bool,
        filled_qty: int,
        *,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return await self._fill_reconciliation.mark_order_partial_filled(
            stock_code, is_buy, filled_qty, exchange=exchange,
        )

    async def mark_order_canceled(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
    ) -> Optional[OrderContext]:
        return await self._fill_reconciliation.mark_order_canceled(stock_code, is_buy, exchange=exchange)

    async def cancel_order(
        self,
        stock_code: Optional[str] = None,
        is_buy: Optional[bool] = None,
        *,
        broker_order_no: Optional[str] = None,
        exchange: Exchange = Exchange.KRX,
        order_orgno: str = "06010",
    ) -> ResCommonResponse:
        return await self._fill_reconciliation.cancel_order(
            stock_code, is_buy, broker_order_no=broker_order_no,
            exchange=exchange, order_orgno=order_orgno,
        )

    async def mark_order_rejected(
        self,
        stock_code,
        is_buy: bool,
        *,
        exchange: Exchange = Exchange.KRX,
        error_message: str = "",
    ) -> Optional[OrderContext]:
        return await self._fill_reconciliation.mark_order_rejected(
            stock_code, is_buy, exchange=exchange, error_message=error_message,
        )

    async def _enqueue_deferred_order(self, **kwargs) -> Optional[ResCommonResponse]:
        return await self._submission_coordinator._enqueue_deferred_order(**kwargs)

    async def _submit_order_with_fsm(self, **kwargs) -> ResCommonResponse:
        return await self._submission_coordinator.submit(**kwargs)

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
        volatility_20d_annualized: Optional[float] = None,
        invalidation_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
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
                volatility_20d_annualized=volatility_20d_annualized,
                invalidation_price=invalidation_price,
                stop_loss_price=stop_loss_price,
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
                if self._notification_service and not self._is_strategy_source(source):
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
        invalidation_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
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
                invalidation_price=invalidation_price,
                stop_loss_price=stop_loss_price,
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
                if self._notification_service and not self._is_strategy_source(source):
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

    async def sell_all_stocks(
        self,
        exchange: Exchange = Exchange.KRX,
        *,
        mode: ClearanceMode = ClearanceMode.SAFE_SEQUENTIAL,
        bounded_concurrency: int = 3,
    ):
        """보유하고 있는 모든 주식을 시장가로 매도합니다.

        mode:
          - SAFE_SEQUENTIAL: 순차 청산 (default, 기존 동작 보존).
          - BOUNDED_PARALLEL: Semaphore(bounded_concurrency) 기반 제한 병렬.
          - EMERGENCY: 전체 동시 청산 (asyncio.gather).
        """
        self.logger.info(f"모든 보유 주식의 일괄 매도를 시작합니다. (mode={mode.value})")
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

            # 2. 매도 대상 정리
            targets: list[tuple[str, int]] = []
            for stock in holdings:
                stock_code = stock.get('pdno')
                quantity = int(stock.get('hldg_qty', 0))
                if stock_code and quantity > 0:
                    targets.append((stock_code, quantity))

            if not targets:
                self.logger.info("매도할 유효한 주식이 없습니다.")
                return {"message": "매도할 유효한 주식이 없습니다.", "results": []}

            # 3. 모드별 매도 실행
            results = await self._dispatch_clearance(targets, exchange, mode, bounded_concurrency)

            self.logger.info(f"일괄 매도 절차가 완료되었습니다. (mode={mode.value}, n={len(results)})")
            self.pm.log_timer(f"OrderExecutionService.sell_all_stocks({mode.value})", t_start)
            return {"message": "일괄 매도가 완료되었습니다.", "results": results}

        except Exception as e:
            self.logger.critical(f"일괄 매도 중 심각한 오류 발생: {e}", exc_info=True)
            return {"error": f"일괄 매도 중 심각한 오류가 발생했습니다: {str(e)}"}

    async def _dispatch_clearance(
        self,
        targets: list[tuple[str, int]],
        exchange: Exchange,
        mode: ClearanceMode,
        bounded_concurrency: int,
    ) -> list[dict]:
        """모드별 매도 실행 후 종목별 결과를 동일 형식으로 집계한다."""
        if mode == ClearanceMode.SAFE_SEQUENTIAL:
            results: list[dict] = []
            for stock_code, quantity in targets:
                results.append(await self._submit_one_sell(stock_code, quantity, exchange))
            return results

        if mode == ClearanceMode.BOUNDED_PARALLEL:
            sem = asyncio.Semaphore(max(1, bounded_concurrency))

            async def _bounded(code: str, qty: int) -> dict:
                async with sem:
                    return await self._submit_one_sell(code, qty, exchange)

            return await asyncio.gather(*[_bounded(c, q) for c, q in targets])

        # EMERGENCY: 전체 동시 청산
        # API budget limiter 의 emergency lane 을 사용해 normal 주문/조회 traffic 과
        # 분리된 슬롯으로 진입한다.
        with emergency_scope():
            return await asyncio.gather(
                *[self._submit_one_sell(c, q, exchange) for c, q in targets]
            )

    async def _submit_one_sell(self, stock_code: str, quantity: int, exchange: Exchange) -> dict:
        """한 종목에 대해 시장가 매도 후 success/실패 dict로 정규화한다."""
        try:
            result = await self.handle_place_sell_order(stock_code, 0, quantity, exchange=exchange)
            if result and result.rt_cd == ErrorCode.SUCCESS.value:
                self.logger.info(f"매도 주문 성공: {stock_code}")
                return {"stock_code": stock_code, "success": True, "message": result.msg1}
            msg = result.msg1 if result else "알 수 없는 오류"
            self.logger.error(f"매도 주문 실패: {stock_code}, 이유: {msg}")
            return {"stock_code": stock_code, "success": False, "message": msg}
        except Exception as e:
            self.logger.error(f"매도 주문 중 예외 발생: {stock_code}, 오류: {str(e)}")
            return {"stock_code": stock_code, "success": False, "message": str(e)}

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
                    self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}")
                elif data_type == 'realtime_quote':  # 주식 호가
                    askp1 = output.get('매도호가1', 'N/A')
                    bidp1 = output.get('매수호가1', 'N/A')
                    trade_time = output.get('영업시간', 'N/A')
                    display_message = (
                        f"\r[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1: {askp1}, 매수1: {bidp1}{' ' * 20}"
                    )
                    self.logger.debug(f"\r{display_message}{' ' * (80 - len(display_message))}")
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
