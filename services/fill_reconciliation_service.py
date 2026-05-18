from datetime import datetime
from typing import Callable, Dict, Optional

from common.types import (
    ErrorCode,
    Exchange,
    OrderContext,
    OrderExecutionReport,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from core.loggers.trace_context import trace_scope
from services.notification_service import NotificationCategory, NotificationLevel


class FillReconciliationService:
    """주문 체결 확인, 폴링, 재시작 복원, 브로커 대사, 취소·상태 정합 책임자.

    Phase 4 (plan: 3-3-playful-walrus.md)에서 OrderExecutionService에서 분리.
    `_reconcile_alarm` 플래그의 소유자이며, OrderSubmissionCoordinator(Phase 5)가
    `is_reconcile_alarm_active()` 로 read-only 게이트로 사용한다.

    의존:
    - state_machine: OrderStateMachine 인스턴스 (Phase 3 분리)
    - execution_quality_reporter: ExecutionQualityReporter (Phase 1 분리)
    - virtual_trade_service / kill_switch_service / account_snapshot_cache:
      체결 확정 후 가상매매 기록·KillSwitch·잔고 캐시 무효화 트리거
    """

    def __init__(
        self,
        broker_api_wrapper,
        logger,
        state_machine,
        execution_quality_reporter,
        *,
        virtual_trade_service=None,
        kill_switch_service=None,
        account_snapshot_cache=None,
        market_clock=None,
        notification_service=None,
        now_provider: Optional[Callable[[], datetime]] = None,
        is_paper_trading_fn: Optional[Callable[[], bool]] = None,
        stuck_order_warning_sec: int = 60,
        stuck_order_critical_sec: int = 180,
    ) -> None:
        self.broker_api_wrapper = broker_api_wrapper
        self.logger = logger
        self._fsm = state_machine
        self._exec_quality_reporter = execution_quality_reporter
        self._virtual_trade_service = virtual_trade_service
        self._kill_switch = kill_switch_service
        self._account_snapshot_cache = account_snapshot_cache
        self.market_clock = market_clock
        self._notification_service = notification_service
        self._now: Callable[[], datetime] = now_provider or datetime.now
        self._is_paper_trading: Callable[[], bool] = is_paper_trading_fn or (lambda: False)
        self._stuck_order_warning_sec = stuck_order_warning_sec
        self._stuck_order_critical_sec = stuck_order_critical_sec

        # ── 소유 상태 ────────────────────────────────────────────────
        self._reconcile_alarm: bool = False
        self._reconcile_consecutive_mismatch_by_key: Dict[str, int] = {}

    # ── reconcile_alarm read API (Coordinator용) ────────────────────
    def is_reconcile_alarm_active(self) -> bool:
        return self._reconcile_alarm

    # ── source 분류 헬퍼 (OES._strategy_name_from_source 와 중복 — 결합 회피 목적의 의도된 사본) ──
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

    # ── 가상매매 영구 기록 (terminal report 시) ──────────────────────
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
        if self._is_reconcile_source(context.source):
            return self._fsm.mark_virtual_trade_recorded(context, context.filled_qty)

        strategy_name, is_strategy_source = self._strategy_name_from_source(context.source)
        record_qty = context.filled_qty
        record_price = report.fill_price or context.price
        if self._kill_switch and report.fill_price and context.price > 0:
            await self._kill_switch.record_fill_event(
                context.price,
                report.fill_price,
                context.stock_code,
                record_qty,
                side=context.side.value,
            )
        sell_result = None
        try:
            if context.side == OrderSide.BUY:
                await self._virtual_trade_service.log_buy_async(
                    strategy_name, context.stock_code, record_price, record_qty,
                    volatility_20d_annualized=context.volatility_20d_annualized,
                )
            elif is_strategy_source:
                sell_result = await self._virtual_trade_service.log_sell_by_strategy_async_with_result(
                    strategy_name, context.stock_code, record_price, record_qty
                )
            else:
                sell_result = await self._virtual_trade_service.log_sell_async_with_result(
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
        # 매도 체결 확정 → KillSwitch 손익 hook
        if (
            self._kill_switch
            and context.side == OrderSide.SELL
            and sell_result is not None
            and sell_result.net_pnl_won is not None
            and sell_result.pnl_filled_qty > 0
        ):
            snapshot = self._account_snapshot_cache.peek() if self._account_snapshot_cache else None
            account_balance_won = snapshot.total_equity if snapshot and snapshot.total_equity > 0 else None
            await self._kill_switch.record_trade_result(
                profit_won=sell_result.net_pnl_won,
                code=context.stock_code,
                strategy=strategy_name or "",
                account_balance_won=account_balance_won,
                count_for_consecutive_loss=(getattr(sell_result, "is_intraday_trade", None) is not False),
            )
            if is_strategy_source and strategy_name:
                await self._kill_switch.record_strategy_trade_result(
                    strategy_name=strategy_name,
                    pnl_won=sell_result.net_pnl_won,
                )
        return self._fsm.mark_virtual_trade_recorded(context, record_qty)

    # ── execution report apply ───────────────────────────────────────
    async def apply_execution_report(self, report: OrderExecutionReport) -> Optional[OrderContext]:
        """체결통보/polling 이벤트를 주문 FSM에 적용합니다."""
        if not report.broker_order_no or not report.stock_code:
            self.logger.warning(f"주문 이벤트 무시: 주문번호/종목코드 누락 - {report.to_dict()}")
            return None

        async with self._fsm.symbol_lock(report.stock_code, report.exchange):
            context = self._fsm.find_context_for_execution_report(report)
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

        if not self._fsm.mark_execution_event_seen(report.event_key):
            return context

        if not context.broker_order_no:
            context = self._fsm.transition(
                context.order_key,
                context.state,
                broker_order_no=report.broker_order_no,
            )

        if report.event_state == OrderState.REJECTED:
            rejected = self._fsm.transition(
                context.order_key,
                OrderState.REJECTED,
                error_message=report.message or "주문 거부",
            )
            self._exec_quality_reporter.log(rejected)
            return rejected

        if report.event_state == OrderState.CANCELED:
            canceled = self._fsm.transition(context.order_key, OrderState.CANCELED)
            self._exec_quality_reporter.log(canceled)
            return await self._persist_virtual_trade_for_terminal_report(canceled, report)

        if report.fill_qty <= 0:
            if context.state == OrderState.PENDING_SUBMIT:
                return self._fsm.transition(context.order_key, OrderState.SUBMITTED)
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
        quality_update = self._exec_quality_reporter.build_update(context, report, filled_qty)
        transitioned = self._fsm.transition(
            context.order_key,
            next_state,
            filled_qty=filled_qty,
            broker_order_no=report.broker_order_no,
            **quality_update,
        )
        self._exec_quality_reporter.log(transitioned)
        return await self._persist_virtual_trade_for_terminal_report(transitioned, report)

    async def handle_signing_notice(self, data: dict, tr_id: str = "") -> Optional[OrderContext]:
        """WebSocket signing_notice payload를 정규화한 뒤 FSM에 적용합니다."""
        return await self.apply_execution_report(
            OrderExecutionReport.from_signing_notice(data, tr_id=tr_id)
        )

    # ── stuck order 감지 ────────────────────────────────────────────
    def _get_stuck_order_alert_level(
        self,
        context: OrderContext,
        age_sec: float,
    ) -> Optional[NotificationLevel]:
        if context.state not in (OrderState.SUBMITTED, OrderState.PARTIAL_FILLED):
            return None
        if age_sec < self._stuck_order_warning_sec:
            return None
        if not self._is_paper_trading() and age_sec >= self._stuck_order_critical_sec:
            return NotificationLevel.CRITICAL
        return NotificationLevel.WARNING

    async def check_stuck_orders_once(self, now: Optional[datetime] = None) -> int:
        now = now or self._now()
        notified_count = 0

        for context in self._fsm.active_contexts():
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
            current_context = self._fsm.lookup(context.order_key)
            if current_context and not current_context.state.is_terminal:
                self._fsm.transition(
                    context.order_key,
                    current_context.state,
                    stuck_alert_at=now,
                    stuck_alert_level=alert_level.value,
                )
            notified_count += 1

        return notified_count

    # ── 주문조회 polling ──────────────────────────────────────────────
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
        contexts = self._fsm.active_contexts()
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
            was_seen = report.event_key in self._fsm._processed_execution_events
            before = self._fsm.find_context_for_execution_report(report)
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

    # ── 재시작 시 broker 상태 복원 ────────────────────────────────────
    async def restore_state_from_broker(self) -> int:
        """시스템 재시작 시 broker 미체결/체결내역에서 주문 상태를 복원합니다.
        paper mode에서는 스킵합니다.
        반환: 복원된 컨텍스트 수
        """
        if self._is_paper_trading():
            self.logger.info("restore_state_from_broker: 모의투자 모드 — 복원 스킵")
            return 0

        now = self._now()
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
                order_key = self._fsm.make_order_key(pdno, side, Exchange.KRX)
                if self._fsm.lookup(order_key) is not None:
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
                self._fsm.register(context)
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
                order_key = self._fsm.make_order_key(pdno, side, Exchange.KRX)
                existing = self._fsm.lookup(order_key)
                if existing:
                    if not existing.state.is_terminal:
                        self._fsm.safe_transition(
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
                self._fsm.register(context)
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

    # ── 브로커 대사 (reconcile_alarm 소유) ────────────────────────────
    async def reconcile_orders_with_broker(self) -> int:
        """활성 주문 상태를 broker 미체결/체결내역/잔고와 비교하여 불일치를 감지합니다.

        정책:
        - 모의투자 모드: 즉시 0 반환 (broker API 호출 없음)
        - 1회 불일치: _reconcile_alarm=True + WARNING, 상태 전이 없음
        - 2회 연속 + 명시 근거(잔고·체결 없음): CANCELED 추정 전이 (assumed=true)
        - 내부 FILLED인데 잔고 없음 → CRITICAL + alarm
        - 잔고에만 있고 내부 컨텍스트 없음 → INFO (외부 주문 가능성)
        - 불일치 0건으로 완료 시: _reconcile_alarm 자동 해제 (일시적 오류 복구)

        반환: 감지된 불일치 건수
        """
        if self._is_paper_trading():
            self.logger.info("reconcile_orders_with_broker: 모의투자 모드 — 스킵")
            return 0

        now = self._now()
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
        active_contexts = self._fsm.active_contexts()
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
            self._fsm._reconcile_mismatch_count += 1
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
                    self._fsm.safe_transition(
                        context.order_key,
                        OrderState.CANCELED,
                        error_message="reconcile assumed=true: broker 미체결/체결 근거 없음",
                    )
                    self._reconcile_consecutive_mismatch_by_key.pop(context.order_key, None)

        # --- 5. 내부 FILLED인데 잔고 없음 → CRITICAL ---
        for order_key, context in self._fsm._order_states.items():
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
                for c in self._fsm._order_states.values()
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

    # ── 외부 이벤트 반영 (resolve / partial / canceled / rejected / cancel API) ─
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
        order_key = self._fsm.make_order_key(stock_code, side, exchange)
        context = self._fsm.lookup(order_key)
        if not context or context.state.is_terminal:
            return context
        qty = context.qty if filled_qty is None and final_state == OrderState.FILLED else (filled_qty or context.filled_qty)
        transitioned = self._fsm.transition(order_key, final_state, filled_qty=qty)
        if transitioned.state.is_terminal or transitioned.average_fill_price is not None:
            self._exec_quality_reporter.log(transitioned)
        return transitioned

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
            order_key = self._fsm._order_no_index.get(broker_order_no)
            context = self._fsm.lookup(order_key) if order_key else None
        if context is None and stock_code is not None and is_buy is not None:
            context = self._fsm.lookup_by_buy_flag(stock_code, is_buy, exchange)

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
        order_key = self._fsm.make_order_key(stock_code, side, exchange)
        context = self._fsm.lookup(order_key)
        if not context or context.state.is_terminal:
            return context
        return self._fsm.transition(
            order_key,
            OrderState.REJECTED,
            error_message=error_message,
        )
