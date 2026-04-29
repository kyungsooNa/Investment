import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import OrderPolicyConfig
from utils.korea_invest_price_utils import adjust_price, get_tick_size


class QuoteProvider(Protocol):
    async def get_asking_price(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        ...


@dataclass
class OrderPolicyDecision:
    allowed: bool
    rule: str = "ok"
    reason: str = "ok"
    adjusted_price: Optional[int] = None
    severity: str = "block"
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_response(self) -> ResCommonResponse:
        data = {
            "gate": "order_policy",
            "rule": self.rule,
            "severity": self.severity,
            "reason": self.reason,
        }
        if self.adjusted_price is not None:
            data["adjusted_price"] = self.adjusted_price
        data.update(self.context)
        return ResCommonResponse(
            rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
            msg1=f"Order Policy 차단: {self.reason}",
            data=data,
        )


class OrderPolicyService:
    """Validate order type, exchange, tick size, and order-book safety before broker submit."""

    def __init__(
        self,
        config: OrderPolicyConfig,
        quote_provider: Optional[QuoteProvider] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._cfg = config
        self._quote_provider = quote_provider
        self._logger = logger or logging.getLogger(__name__)

    async def validate_order(
        self,
        *,
        stock_code: str,
        price: int,
        qty: int,
        side: OrderSide,
        exchange: Exchange,
    ) -> OrderPolicyDecision:
        if not self._cfg.enabled:
            return OrderPolicyDecision(allowed=True, adjusted_price=price)

        if qty <= 0:
            return self._blocked("non_positive_qty", "주문 수량은 0보다 커야 합니다.", qty=qty)
        if price < 0:
            return self._blocked("negative_price", "주문 가격은 음수일 수 없습니다.", price=price)

        order_type = "market" if price == 0 else "limit"
        if order_type == "market":
            order_type_blocked = self._check_market_order_type(side, exchange)
            if order_type_blocked is not None:
                return order_type_blocked
            if self._cfg.order_book_checks_enabled:
                quote_decision = await self._check_order_book(
                    stock_code=stock_code,
                    price=price,
                    qty=qty,
                    side=side,
                    exchange=exchange,
                    order_type=order_type,
                )
                if quote_decision.blocked:
                    return quote_decision
                return quote_decision
            return OrderPolicyDecision(
                allowed=True,
                rule="market_order",
                reason="market_order_allowed",
                adjusted_price=price,
                severity="pass",
                context={"order_type": "market"},
            )

        tick_decision = self._check_limit_tick_size(stock_code, price)
        if tick_decision.blocked:
            return tick_decision

        if self._cfg.order_book_checks_enabled:
            quote_decision = await self._check_order_book(
                stock_code=stock_code,
                price=tick_decision.adjusted_price if tick_decision.adjusted_price is not None else price,
                qty=qty,
                side=side,
                exchange=exchange,
                order_type=order_type,
            )
            if quote_decision.blocked:
                return quote_decision

        return tick_decision

    def _check_market_order_type(
        self,
        side: OrderSide,
        exchange: Exchange,
    ) -> Optional[OrderPolicyDecision]:
        if exchange == Exchange.NXT and not self._cfg.allow_nxt_market_order:
            return self._blocked(
                "nxt_market_order_not_supported",
                "NXT 거래소에서는 시장가 주문을 허용하지 않습니다.",
                exchange=exchange.value,
            )
        if side == OrderSide.BUY and not self._cfg.allow_market_buy:
            return self._blocked("market_buy_disabled", "시장가 매수 주문이 비활성화되어 있습니다.")
        if side == OrderSide.SELL and not self._cfg.allow_market_sell:
            return self._blocked("market_sell_disabled", "시장가 매도 주문이 비활성화되어 있습니다.")
        return None

    def _check_limit_tick_size(self, stock_code: str, price: int) -> OrderPolicyDecision:
        tick_size = get_tick_size(price)
        adjusted = adjust_price(price)
        context = {
            "order_type": "limit",
            "stock_code": stock_code,
            "requested_price": price,
            "tick_size": tick_size,
            "adjusted_price": adjusted,
        }
        if adjusted == price or self._cfg.tick_size_policy == "ignore":
            return OrderPolicyDecision(
                allowed=True,
                rule="limit_tick_size",
                reason="limit_order_allowed",
                adjusted_price=price,
                severity="pass",
                context=context,
            )
        if self._cfg.tick_size_policy == "block":
            return self._blocked(
                "invalid_tick_size",
                "지정가가 호가단위에 맞지 않습니다.",
                **context,
            )

        self._logger.info(
            f"[OrderPolicy][ADJUST] rule=tick_size stock_code={stock_code} "
            f"requested_price={price} adjusted_price={adjusted} tick_size={tick_size}"
        )
        return OrderPolicyDecision(
            allowed=True,
            rule="tick_size_adjusted",
            reason="호가단위 보정",
            adjusted_price=adjusted,
            severity="adjust",
            context=context,
        )

    async def _check_order_book(
        self,
        stock_code: str,
        price: int,
        qty: int,
        side: OrderSide,
        exchange: Exchange,
        order_type: str,
    ) -> OrderPolicyDecision:
        if self._quote_provider is None:
            return self._quote_failure_decision(stock_code, "quote_provider_missing")

        try:
            quote_resp = self._quote_provider.get_asking_price(stock_code, exchange=exchange)
            if inspect.isawaitable(quote_resp):
                quote_resp = await quote_resp
        except Exception as exc:
            return self._quote_failure_decision(stock_code, str(exc))

        if not quote_resp or quote_resp.rt_cd != ErrorCode.SUCCESS.value:
            reason = quote_resp.msg1 if quote_resp else "응답 없음"
            return self._quote_failure_decision(stock_code, reason)

        quote = self._extract_quote_data(quote_resp.data)
        trading_value = self._to_int(self._pick(
            quote,
            "acml_tr_pbmn",
            "trading_value",
            "acc_trading_value",
            "누적거래대금",
        ))
        ask = self._to_int(self._pick(quote, "askp1", "매도호가1"))
        bid = self._to_int(self._pick(quote, "bidp1", "매수호가1"))
        ask_qty = self._to_int(self._pick(quote, "askp_rsqn1", "매도호가잔량1"))
        bid_qty = self._to_int(self._pick(quote, "bidp_rsqn1", "매수호가잔량1"))
        current_price = self._to_int(self._pick(quote, "stck_prpr", "주식현재가", "prpr"))

        if self._cfg.block_empty_order_book and (ask <= 0 or bid <= 0):
            return self._blocked(
                "empty_order_book",
                "최우선 매수/매도 호가가 비어 있습니다.",
                stock_code=stock_code,
                ask=ask,
                bid=bid,
            )

        mid = (ask + bid) / 2 if ask > 0 and bid > 0 else current_price
        spread_pct = ((ask - bid) / mid * 100) if mid and ask > 0 and bid > 0 else 0.0
        if spread_pct > self._cfg.max_spread_pct:
            return self._blocked(
                "spread_too_wide",
                "스프레드가 허용 범위를 초과했습니다.",
                stock_code=stock_code,
                ask=ask,
                bid=bid,
                spread_pct=round(spread_pct, 3),
                max_spread_pct=self._cfg.max_spread_pct,
            )

        executable_price = ask if side == OrderSide.BUY else bid
        reference_price = current_price or mid
        slippage_pct = 0.0
        if order_type == "market":
            slippage_pct = (
                abs(executable_price - reference_price) / reference_price * 100
                if reference_price and executable_price
                else 0.0
            )
        if order_type == "market" and slippage_pct > self._cfg.max_market_slippage_pct:
            return self._blocked(
                "market_slippage_too_high",
                "시장가 예상 슬리피지가 허용 범위를 초과했습니다.",
                stock_code=stock_code,
                side=side.value,
                executable_price=executable_price,
                reference_price=reference_price,
                slippage_pct=round(slippage_pct, 3),
                max_market_slippage_pct=self._cfg.max_market_slippage_pct,
            )

        if self._cfg.min_trading_value_won > 0:
            if trading_value <= 0:
                return self._blocked(
                    "trading_value_unavailable",
                    "거래대금 데이터가 없어 유동성 검증을 통과할 수 없습니다.",
                    stock_code=stock_code,
                    min_trading_value_won=self._cfg.min_trading_value_won,
                )
            if trading_value < self._cfg.min_trading_value_won:
                return self._blocked(
                    "trading_value_too_low",
                    "거래대금이 최소 유동성 기준보다 작습니다.",
                    stock_code=stock_code,
                    trading_value_won=trading_value,
                    min_trading_value_won=self._cfg.min_trading_value_won,
                )

        book_qty = ask_qty if side == OrderSide.BUY else bid_qty
        if book_qty > 0 and qty > book_qty:
            return self._blocked(
                "top_of_book_qty_short",
                "최우선 호가 잔량보다 주문 수량이 큽니다.",
                stock_code=stock_code,
                side=side.value,
                qty=qty,
                top_of_book_qty=book_qty,
            )
        if book_qty > 0 and self._cfg.max_top_of_book_participation_pct > 0:
            participation_pct = qty / book_qty * 100
            if participation_pct > self._cfg.max_top_of_book_participation_pct:
                return self._blocked(
                    "top_of_book_participation_too_high",
                    "주문 수량이 최우선 호가 잔량 대비 허용 비율을 초과합니다.",
                    stock_code=stock_code,
                    side=side.value,
                    qty=qty,
                    top_of_book_qty=book_qty,
                    participation_pct=round(participation_pct, 3),
                    max_top_of_book_participation_pct=self._cfg.max_top_of_book_participation_pct,
                )

        return OrderPolicyDecision(
            allowed=True,
            rule=f"{order_type}_order_book",
            reason=f"{order_type}_order_book_allowed",
            adjusted_price=price,
            severity="pass",
            context={
                "order_type": order_type,
                "stock_code": stock_code,
                "ask": ask,
                "bid": bid,
                "executable_price": executable_price,
                "reference_price": reference_price,
                "top_of_book_qty": book_qty,
                "trading_value_won": trading_value,
                "spread_pct": round(spread_pct, 3),
                "slippage_pct": round(slippage_pct, 3),
            },
        )

    def _quote_failure_decision(self, stock_code: str, reason: str) -> OrderPolicyDecision:
        if self._cfg.quote_fail_policy == "block":
            return self._blocked(
                "quote_unavailable",
                "호가 조회 실패로 주문을 검증할 수 없습니다.",
                stock_code=stock_code,
                quote_error=reason,
            )
        self._logger.warning(
            f"[OrderPolicy][CHECK_ERROR] rule=quote_unavailable "
            f"stock_code={stock_code} reason={reason}"
        )
        return OrderPolicyDecision(
            allowed=True,
            rule="quote_unavailable",
            reason="quote_check_fail_open",
            adjusted_price=0,
            severity="pass",
            context={"stock_code": stock_code, "quote_error": reason},
        )

    @staticmethod
    def _extract_quote_data(data) -> dict:
        if isinstance(data, dict):
            if isinstance(data.get("output1"), dict):
                return data["output1"]
            if isinstance(data.get("output"), dict):
                return data["output"]
            return data
        return {}

    @staticmethod
    def _pick(data: dict, *keys: str):
        for key in keys:
            if key in data:
                return data.get(key)
        return None

    @staticmethod
    def _to_int(value) -> int:
        try:
            return int(float(str(value or "0").replace(",", "")))
        except (TypeError, ValueError):
            return 0

    def _blocked(self, rule: str, reason: str, **context) -> OrderPolicyDecision:
        self._logger.warning(
            f"[OrderPolicy][BLOCK] rule={rule} reason={reason} context={context}"
        )
        return OrderPolicyDecision(
            allowed=False,
            rule=rule,
            reason=reason,
            context=context,
        )
