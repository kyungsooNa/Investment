import uuid
from typing import Callable, Optional

from common.types import (
    ErrorCode,
    Exchange,
    OrderContext,
    OrderSide,
    OrderState,
    ResCommonResponse,
)
from services.notification_service import NotificationCategory, NotificationLevel


class OrderSubmissionCoordinator:
    """주문 제출 흐름의 코디네이터.

    Phase 5 (plan: 3-3-playful-walrus.md)에서 OrderExecutionService의 `_submit_order_with_fsm`을 분리.
    데이터 품질·주문 정책·리스크 게이트·intent dedup·양방향 차단·deferred enqueue·FSM 등록·broker 제출을
    순차 실행한다. 외부 부수효과는 의존 서비스에 위임하고 본인은 흐름 조정만 담당한다.
    """

    def __init__(
        self,
        logger,
        broker_api_wrapper,
        state_machine,
        broker_submitter,
        execution_quality_reporter,
        fill_reconciliation_service,
        *,
        deferred_order_queue=None,
        data_quality_service=None,
        order_policy_service=None,
        risk_gate_service=None,
        notification_service=None,
        is_paper_trading_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.logger = logger
        self.broker_api_wrapper = broker_api_wrapper
        self._fsm = state_machine
        self._broker_submitter = broker_submitter
        self._exec_quality_reporter = execution_quality_reporter
        self._fill_reconciliation = fill_reconciliation_service
        self._deferred_queue = deferred_order_queue
        self._data_quality = data_quality_service
        self._order_policy = order_policy_service
        self._risk_gate = risk_gate_service
        self._notification_service = notification_service
        self._is_paper_trading: Callable[[], bool] = is_paper_trading_fn or (lambda: False)

    # ── source helpers (RiskGate strategy 분류용) ───────────────────
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

    # ── pre-submit log + finalize 정책 ───────────────────────────────
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
        if self._is_paper_trading():
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
        if self._is_paper_trading():
            return requested
        if requested:
            self.logger.warning(
                "실전 모드: finalize_immediately=True 요청을 무시합니다. "
                "체결 확정은 WebSocket/polling 체결 통보로만 처리됩니다."
            )
        return False

    # ── deferred enqueue ────────────────────────────────────────────
    async def _enqueue_deferred_order(
        self,
        *,
        stock_code,
        price,
        qty,
        exchange: Exchange,
        side: OrderSide,
        source: str,
        existing,
        invalidation_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> Optional[ResCommonResponse]:
        """진행 중 주문이 있을 때 신규 주문을 deferred queue 에 enqueue.

        QUEUED → ORDER_DEFERRED 응답 반환.
        DUPLICATE_DROPPED → None 반환 (호출자가 기본 차단 응답으로 fallback).
        """
        from services.deferred_order_queue import EnqueueResult

        async def _retry():
            return await self.submit(
                stock_code=stock_code,
                price=price,
                qty=qty,
                exchange=exchange,
                side=side,
                source=source,
                finalize_immediately=False,
                invalidation_price=invalidation_price,
                stop_loss_price=stop_loss_price,
            )

        result = await self._deferred_queue.enqueue(
            stock_code=str(stock_code),
            side=side.value,
            submit_callable=_retry,
            description=f"source={source} qty={qty} price={price} blocked_by={existing.order_key}",
        )
        if result == EnqueueResult.QUEUED:
            return ResCommonResponse(
                rt_cd=ErrorCode.ORDER_DEFERRED.value,
                msg1=(
                    f"동일 종목 진행 주문으로 보류 큐 등록: "
                    f"code={stock_code} side={side.value} blocked_by={existing.order_key}"
                ),
                data=existing.to_dict(),
            )
        return None

    # ── main entry ──────────────────────────────────────────────────
    async def submit(
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
        volatility_20d_annualized: Optional[float] = None,
        invalidation_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        strategy_notification: Optional[dict] = None,
    ) -> ResCommonResponse:
        order_key = self._fsm.make_order_key(stock_code, side, exchange)
        action_kr = "매수" if side == OrderSide.BUY else "매도"
        resolved_intent_id = intent_id or str(uuid.uuid4())

        if self._fill_reconciliation.is_reconcile_alarm_active():
            self.logger.warning(
                f"reconcile alarm 활성 → 신규 주문 차단: stock_code={stock_code}, side={side.value}"
            )
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="reconcile alarm 활성: 불일치 감지로 신규 주문이 차단되었습니다.",
                data=None,
            )

        async with self._fsm.symbol_lock(stock_code, exchange):
            # 중복 intent 차단: 동일 intent_id + 활성 상태이면 즉시 거부
            existing_key_for_intent = self._fsm.intent_to_order_key(resolved_intent_id)
            if existing_key_for_intent:
                existing_ctx = self._fsm.lookup(existing_key_for_intent)
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
                existing = self._fsm.lookup_by_side(stock_code, existing_side, exchange)
                if existing and not existing.state.is_terminal:
                    self.logger.warning(
                        f"{action_kr} 주문 차단: 진행 중인 주문이 있습니다. "
                        f"종목={stock_code}, 기존상태={existing.state.value}, 기존주문={existing.order_key}"
                    )
                    if self._deferred_queue is not None:
                        deferred_resp = await self._enqueue_deferred_order(
                            stock_code=stock_code,
                            price=price,
                            qty=qty,
                            exchange=exchange,
                            side=side,
                            source=source,
                            existing=existing,
                            invalidation_price=invalidation_price,
                            stop_loss_price=stop_loss_price,
                        )
                        if deferred_resp is not None:
                            return deferred_resp
                    return ResCommonResponse(
                        rt_cd=ErrorCode.RETRY_LIMIT.value,
                        msg1=f"진행 중인 주문이 있어 {action_kr} 주문을 차단했습니다. 상태={existing.state.value}",
                        data=existing.to_dict(),
                    )

            if self._data_quality is not None:
                quality = await self._data_quality.validate_order_reference(
                    stock_code=stock_code,
                    price=price,
                    qty=qty,
                )
                if not quality.ok:
                    msg = f"데이터 품질 차단: {quality.reason}"
                    self.logger.error(
                        f"{msg} stock_code={stock_code}, side={side.value}, "
                        f"latency={quality.latency_sec}, metadata={quality.metadata}"
                    )
                    if self._notification_service:
                        await self._notification_service.emit(
                            NotificationCategory.SYSTEM,
                            NotificationLevel.ERROR if quality.severity == "error" else NotificationLevel.WARNING,
                            "데이터 품질 주문 차단",
                            f"{stock_code} {side.value}: {quality.reason}",
                            metadata=quality.to_dict(),
                        )
                    return ResCommonResponse(
                        rt_cd=ErrorCode.INVALID_INPUT.value,
                        msg1=msg,
                        data=quality.to_dict(),
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
                    active_order_count=len(self._fsm.active_contexts()),
                    source=source,
                    strategy_name=strategy_name if is_strategy_source else None,
                    invalidation_price=invalidation_price,
                    stop_loss_price=stop_loss_price,
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
                expected_fill_price=self._exec_quality_reporter.resolve_expected_fill_price(price, policy_decision),
                order_type=self._exec_quality_reporter.resolve_order_type(price, policy_decision),
                spread_pct=self._exec_quality_reporter.resolve_spread_pct(policy_decision),
                volatility_20d_annualized=volatility_20d_annualized,
                strategy_notification=dict(strategy_notification or {}),
            )
            self._fsm.register(context)
            self._fsm.register_intent(resolved_intent_id, order_key)

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

            result = await self._broker_submitter.submit_with_retry(
                stock_code,
                price,
                qty,
                is_buy=(side == OrderSide.BUY),
                exchange=exchange,
                order_key=order_key,
            )

            latest = self._fsm.lookup(order_key)
            if result and result.rt_cd == ErrorCode.SUCCESS.value:
                if latest and latest.state == OrderState.SUBMITTED:
                    self._fsm.register_post_submit_fast_poll(order_key)
                if self._resolve_finalize(finalize_immediately) and latest and latest.state == OrderState.SUBMITTED:
                    self._fsm.transition(order_key, OrderState.FILLED, filled_qty=qty)
                return result

            if latest and latest.state == OrderState.PENDING_SUBMIT:
                self._fsm.transition(
                    order_key,
                    OrderState.REJECTED,
                    error_code=result.rt_cd if result else None,
                    error_message=result.msg1 if result else "응답 없음",
                )
            return result
