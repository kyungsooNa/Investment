import asyncio
from datetime import datetime
from typing import Callable, Optional

from common.types import OrderContext, OrderExecutionReport, OrderSide, OrderState
from config.config_loader import ExecutionQualityReportConfig
from services.notification_service import NotificationCategory, NotificationLevel, NotificationService
from services.order_policy_service import OrderPolicyDecision


class ExecutionQualityReporter:
    """주문 실행 품질(슬리피지·체결지연·미체결비율) 메트릭 산출·로깅·알람 책임자.

    OrderExecutionService에서 Phase 1(plan: 3-3-playful-walrus.md)로 분리.
    주문 FSM 상태는 변경하지 않는다(alert dedup용 내부 상태 `_alerted`만 보유).
    """

    def __init__(
        self,
        logger,
        config: Optional[ExecutionQualityReportConfig] = None,
        notification_service: Optional[NotificationService] = None,
        *,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.logger = logger
        self._config = config or ExecutionQualityReportConfig()
        self._notification_service = notification_service
        self._now: Callable[[], datetime] = now_provider or datetime.now
        self._alerted: set[tuple[str, str]] = set()
        self._notification_tasks: set[asyncio.Task] = set()

    # ── pre-submit context derivation ─────────────────────────────────
    def resolve_expected_fill_price(
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

    def resolve_order_type(
        self,
        price: int,
        policy_decision: Optional[OrderPolicyDecision],
    ) -> str:
        context = policy_decision.context if policy_decision else {}
        order_type = str((context or {}).get("order_type") or "").strip()
        if order_type:
            return order_type
        return "market" if price == 0 else "limit"

    def resolve_spread_pct(
        self,
        policy_decision: Optional[OrderPolicyDecision],
    ) -> Optional[float]:
        if policy_decision is None:
            return None
        try:
            value = (policy_decision.context or {}).get("spread_pct")
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    # ── post-execution metric build + log ─────────────────────────────
    def build_update(
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
            first_fill_latency = (self._now() - context.created_at).total_seconds()

        return {
            "average_fill_price": average_fill_price,
            "total_fill_amount": total_fill_amount,
            "last_fill_price": int(fill_price) if fill_price else None,
            "slippage_amount_won": slippage_amount,
            "slippage_pct": slippage_pct,
            "first_fill_latency_sec": first_fill_latency,
        }

    def log(self, context: OrderContext) -> None:
        if context.average_fill_price is None and not context.state.is_terminal:
            return
        now = self._now()
        order_age_sec = (now - context.created_at).total_seconds() if context.created_at else None
        fill_ratio_pct = (context.filled_qty / context.qty * 100) if context.qty > 0 else None
        unfilled_ratio_pct = (context.remaining_qty / context.qty * 100) if context.qty > 0 else None
        event = {
            "event": "execution_quality",
            "order_key": context.order_key,
            "code": context.stock_code,
            "side": context.side.value,
            "source": context.source,
            "state": context.state.value,
            "order_type": context.order_type,
            "spread_pct": context.spread_pct,
            "order_qty": context.qty,
            "expected_fill_price": context.expected_fill_price,
            "average_fill_price": context.average_fill_price,
            "filled_qty": context.filled_qty,
            "remaining_qty": context.remaining_qty,
            "fill_ratio_pct": fill_ratio_pct,
            "unfilled_ratio_pct": unfilled_ratio_pct,
            "order_age_sec": order_age_sec,
            "slippage_amount_won": context.slippage_amount_won,
            "slippage_pct": context.slippage_pct,
            "first_fill_latency_sec": context.first_fill_latency_sec,
            "trace_id": context.trace_id,
        }
        self.logger.info(event)
        log_dir = getattr(self.logger, "log_dir", None)
        if isinstance(log_dir, str):
            try:
                from core.logger import get_strategy_logger
                get_strategy_logger("ExecutionQuality", log_dir=log_dir).info(event)
            except Exception as exc:
                self.logger.warning(f"execution_quality 전략 로그 기록 실패: {exc}")
        avg_fill_price_str = self._fmt_optional(context.average_fill_price)
        spread_pct_str = self._fmt_optional(context.spread_pct)
        fill_ratio_pct_str = self._fmt_optional(fill_ratio_pct)
        unfilled_ratio_pct_str = self._fmt_optional(unfilled_ratio_pct)
        order_age_sec_str = self._fmt_optional(order_age_sec)
        slippage_won_str = self._fmt_optional(context.slippage_amount_won)
        slippage_pct_str = self._fmt_optional(context.slippage_pct)
        first_fill_latency_str = self._fmt_optional(context.first_fill_latency_sec)
        self.logger.info(
            f"[EXECUTION QUALITY] order_key={context.order_key} code={context.stock_code} "
            f"side={context.side.value} state={context.state.value} "
            f"order_type={context.order_type} spread_pct={spread_pct_str} "
            f"expected_price={context.expected_fill_price} avg_fill_price={avg_fill_price_str} "
            f"filled_qty={context.filled_qty} remaining_qty={context.remaining_qty} "
            f"fill_ratio_pct={fill_ratio_pct_str} unfilled_ratio_pct={unfilled_ratio_pct_str} "
            f"order_age_sec={order_age_sec_str} "
            f"slippage_won={slippage_won_str} slippage_pct={slippage_pct_str} "
            f"first_fill_latency_sec={first_fill_latency_str} source={context.source} "
            f"trace_id={context.trace_id}"
        )
        self.emit_alert(context, event)

    def emit_alert(self, context: OrderContext, event: dict) -> None:
        if not self._notification_service or not getattr(self._config, "enabled", True):
            return
        breaches = self._find_breaches(event)
        if not breaches:
            return

        new_breaches = []
        for breach in breaches:
            key = (context.order_key, breach["metric"])
            if key in self._alerted:
                continue
            self._alerted.add(key)
            new_breaches.append(breach)
        if not new_breaches:
            return

        level = NotificationLevel.ERROR if any(item.get("severity") == "error" for item in new_breaches) else NotificationLevel.WARNING
        message = ", ".join(
            f"{item['metric']}={item['value']:.2f} (기준 {item['threshold']:.2f})"
            for item in new_breaches
        )
        metadata = {
            **event,
            "breaches": new_breaches,
        }
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._notification_service.emit(
                NotificationCategory.TRADE,
                level,
                "체결 품질 임계 초과",
                f"{context.stock_code} {context.side.value}: {message}",
                metadata=metadata,
            ))
            self._notification_tasks.add(task)
            task.add_done_callback(self._notification_tasks.discard)
        except RuntimeError:
            self.logger.warning(f"체결 품질 알림 발행 실패(이벤트 루프 없음): {metadata}")

    # ── internal helpers ──────────────────────────────────────────────
    def _find_breaches(self, event: dict) -> list[dict]:
        cfg = self._config
        breaches: list[dict] = []

        def _add(metric: str, value, warn_threshold, error_threshold=None) -> None:
            if value is None or warn_threshold is None:
                return
            try:
                numeric = float(value)
                warn = float(warn_threshold)
            except (TypeError, ValueError):
                return
            if warn <= 0 or numeric < warn:
                return
            severity = "warning"
            if error_threshold is not None:
                try:
                    if numeric >= float(error_threshold):
                        severity = "error"
                except (TypeError, ValueError):
                    pass
            breaches.append({
                "metric": metric,
                "value": numeric,
                "threshold": warn,
                "severity": severity,
            })

        slippage_pct = event.get("slippage_pct")
        try:
            adverse_slippage = slippage_pct is not None and float(slippage_pct) > 0
        except (TypeError, ValueError):
            adverse_slippage = False
        if adverse_slippage:
            _add(
                "slippage_pct",
                slippage_pct,
                cfg.warn_avg_slippage_pct,
                cfg.candidate_avg_slippage_pct,
            )
        _add(
            "first_fill_latency_sec",
            event.get("first_fill_latency_sec"),
            cfg.warn_avg_first_fill_latency_sec,
            cfg.candidate_avg_first_fill_latency_sec,
        )
        if event.get("state") in {OrderState.CANCELED.value, OrderState.REJECTED.value}:
            _add(
                "incomplete_fill_ratio_pct",
                event.get("unfilled_ratio_pct"),
                cfg.warn_incomplete_fill_ratio_pct,
                cfg.candidate_incomplete_fill_ratio_pct,
            )
        return breaches

    @staticmethod
    def _fmt_optional(value, precision: int = 2) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.{precision}f}"
        except (TypeError, ValueError):
            return str(value)
