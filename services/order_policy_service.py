import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import OrderPolicyConfig
from services.execution_flow_service import ExecutionFlowSnapshot
from utils.korea_invest_price_utils import adjust_price, get_tick_size


class QuoteProvider(Protocol):
    async def get_asking_price(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        ...


class SecurityInfoProvider(Protocol):
    async def get_current_price(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        ...


class TradeFlowProvider(Protocol):
    async def get_snapshot(
        self,
        stock_code: str,
        exchange: Exchange = Exchange.KRX,
        *,
        force_refresh: bool = False,
    ) -> ExecutionFlowSnapshot:
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
        security_info_provider: Optional[SecurityInfoProvider] = None,
        trade_flow_provider: Optional[TradeFlowProvider] = None,
        logger: Optional[logging.Logger] = None,
        env: Optional[Any] = None,
    ):
        self._cfg = config
        self._quote_provider = quote_provider
        self._security_info_provider = security_info_provider
        self._trade_flow_provider = trade_flow_provider
        self._logger = logger or logging.getLogger(__name__)
        self._env = env

    def _is_real_mode(self) -> bool:
        env = self._env
        if env is None:
            return False
        return not bool(getattr(env, "is_paper_trading", True))

    def _effective_allow_market_buy(self) -> bool:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.allow_market_buy
        return self._cfg.allow_market_buy

    def _effective_max_market_slippage_pct(self) -> float:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.max_market_slippage_pct
        return self._cfg.max_market_slippage_pct

    def _effective_max_spread_pct(self) -> float:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.max_spread_pct
        return self._cfg.max_spread_pct

    def _effective_max_top_of_book_participation_pct(self) -> float:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.max_top_of_book_participation_pct
        return self._cfg.max_top_of_book_participation_pct

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

        security_context: dict[str, Any] = {}
        if self._cfg.security_status_checks_enabled:
            security_decision = await self._check_security_status(stock_code, exchange, side)
            security_context = security_decision.context
            if security_decision.blocked:
                return security_decision

        trade_flow_context: dict[str, Any] = {}
        if self._cfg.trade_flow_checks_enabled:
            trade_flow_decision = await self._check_trade_flow(stock_code, exchange)
            trade_flow_context = trade_flow_decision.context
            if trade_flow_decision.blocked:
                return trade_flow_decision

        order_type = "market" if price == 0 else "limit"
        common_context = {**security_context, **trade_flow_context}
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
                return self._with_context(quote_decision, common_context)
            return OrderPolicyDecision(
                allowed=True,
                rule="market_order",
                reason="market_order_allowed",
                adjusted_price=price,
                severity="pass",
                context={"order_type": "market", **common_context},
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
            return self._with_context(quote_decision, common_context)

        return self._with_context(tick_decision, common_context)

    async def _check_trade_flow(
        self,
        stock_code: str,
        exchange: Exchange,
    ) -> OrderPolicyDecision:
        if self._trade_flow_provider is None:
            return self._trade_flow_failure_decision(stock_code, "trade_flow_provider_missing")

        try:
            snapshot = self._trade_flow_provider.get_snapshot(stock_code, exchange=exchange)
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
        except TypeError:
            try:
                snapshot = self._trade_flow_provider.get_snapshot(stock_code)
                if inspect.isawaitable(snapshot):
                    snapshot = await snapshot
            except Exception as exc:
                return self._trade_flow_failure_decision(stock_code, str(exc))
        except Exception as exc:
            return self._trade_flow_failure_decision(stock_code, str(exc))

        if snapshot is None:
            return self._trade_flow_failure_decision(stock_code, "snapshot_missing")

        context = {
            "stock_code": stock_code,
            **snapshot.to_policy_context(),
        }

        unavailable_flags = {"conclusion_unavailable", "time_concluded_unavailable"}
        if unavailable_flags.issubset(set(snapshot.quality_flags)):
            return self._trade_flow_failure_decision(stock_code, "all_trade_flow_sources_unavailable")
        if "time_concluded_unavailable" in snapshot.quality_flags and (
            self._cfg.max_last_trade_age_sec > 0
            or self._cfg.min_recent_trade_count > 0
            or self._cfg.min_trade_value_per_min_won > 0
        ):
            return self._trade_flow_failure_decision(stock_code, "time_concluded_unavailable")
        if "conclusion_unavailable" in snapshot.quality_flags and self._cfg.min_execution_strength_pct > 0:
            return self._trade_flow_failure_decision(stock_code, "conclusion_unavailable")

        if (
            self._cfg.max_last_trade_age_sec > 0
            and snapshot.last_trade_age_sec is not None
            and snapshot.last_trade_age_sec > self._cfg.max_last_trade_age_sec
        ):
            return self._blocked(
                "trade_flow_stale",
                "최근 체결 데이터가 오래되어 주문을 차단합니다.",
                max_last_trade_age_sec=self._cfg.max_last_trade_age_sec,
                **context,
            )

        if (
            self._cfg.min_recent_trade_count > 0
            and snapshot.recent_trade_count is not None
            and snapshot.recent_trade_count < self._cfg.min_recent_trade_count
            and not self._has_strong_execution_strength(snapshot)
        ):
            return self._blocked(
                "trade_flow_velocity_too_low",
                "최근 체결 건수가 최소 기준보다 작습니다.",
                min_recent_trade_count=self._cfg.min_recent_trade_count,
                **context,
            )

        if self._cfg.min_trade_value_per_min_won > 0:
            value_per_min = snapshot.recent_trade_value_won
            if value_per_min is not None:
                value_per_min = value_per_min / (snapshot.sample_window_sec / 60)
            if value_per_min is not None and value_per_min < self._cfg.min_trade_value_per_min_won:
                return self._blocked(
                    "trade_flow_value_velocity_too_low",
                    "최근 분당 체결대금이 최소 기준보다 작습니다.",
                    min_trade_value_per_min_won=self._cfg.min_trade_value_per_min_won,
                    recent_trade_value_per_min_won=int(value_per_min),
                    **context,
                )

        if (
            self._cfg.min_execution_strength_pct > 0
            and snapshot.execution_strength_pct is not None
            and snapshot.execution_strength_pct < self._cfg.min_execution_strength_pct
        ):
            return self._blocked(
                "trade_flow_strength_too_low",
                "체결강도가 최소 기준보다 작습니다.",
                min_execution_strength_pct=self._cfg.min_execution_strength_pct,
                **context,
            )

        return OrderPolicyDecision(
            allowed=True,
            rule="trade_flow",
            reason="trade_flow_allowed",
            severity="pass",
            context=context,
        )

    def _has_strong_execution_strength(self, snapshot: ExecutionFlowSnapshot) -> bool:
        strength = snapshot.execution_strength_pct
        if strength is None:
            return False
        threshold = self._cfg.min_execution_strength_pct if self._cfg.min_execution_strength_pct > 0 else 120.0
        return strength >= threshold

    async def _check_security_status(
        self,
        stock_code: str,
        exchange: Exchange,
        side: OrderSide,
    ) -> OrderPolicyDecision:
        if self._security_info_provider is None:
            return self._security_status_failure_decision(stock_code, "security_info_provider_missing")

        try:
            price_resp = self._security_info_provider.get_current_price(stock_code, exchange=exchange)
            if inspect.isawaitable(price_resp):
                price_resp = await price_resp
        except TypeError:
            try:
                price_resp = self._security_info_provider.get_current_price(stock_code)
                if inspect.isawaitable(price_resp):
                    price_resp = await price_resp
            except Exception as exc:
                return self._security_status_failure_decision(stock_code, str(exc))
        except Exception as exc:
            return self._security_status_failure_decision(stock_code, str(exc))

        if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
            reason = price_resp.msg1 if price_resp else "응답 없음"
            return self._security_status_failure_decision(stock_code, reason)

        output = self._extract_stock_output(price_resp.data)
        iscd_stat_cls_code = str(self._pick(output, "iscd_stat_cls_code") or "").strip()
        mang_issu_cls_code = str(self._pick(output, "mang_issu_cls_code") or "").strip()
        mrkt_warn_cls_code = str(self._pick(output, "mrkt_warn_cls_code") or "").strip()
        invt_caful_yn = str(self._pick(output, "invt_caful_yn") or "").strip().upper()
        market_cap = self._to_int(self._pick(output, "hts_avls", "stck_llam", "market_cap"))

        context = {
            "stock_code": stock_code,
            "iscd_stat_cls_code": iscd_stat_cls_code,
            "mang_issu_cls_code": mang_issu_cls_code,
            "mrkt_warn_cls_code": mrkt_warn_cls_code,
            "invt_caful_yn": invt_caful_yn,
            "market_cap_won": market_cap,
        }

        sell_blocked_status_codes = {
            str(code).strip()
            for code in getattr(self._cfg, "blocked_sell_stock_status_codes", ["58"])
        }
        if side == OrderSide.SELL and iscd_stat_cls_code in sell_blocked_status_codes:
            return self._blocked(
                "trading_halted_stock",
                "거래정지 상태 종목은 주문할 수 없습니다.",
                blocked_sell_stock_status_codes=sorted(sell_blocked_status_codes),
                **context,
            )

        if side == OrderSide.SELL:
            return OrderPolicyDecision(
                allowed=True,
                rule="security_status",
                reason="security_status_allowed_for_sell",
                severity="pass",
                context=context,
            )

        if self._cfg.block_managed_issue and self._is_flagged_code(mang_issu_cls_code):
            return self._blocked(
                "managed_issue_stock",
                "관리종목은 주문할 수 없습니다.",
                **context,
            )

        blocked_status_codes = {
            self._normalize_status_code(code)
            for code in self._cfg.blocked_stock_status_codes
        }
        blocked_market_warning_codes = {
            self._normalize_status_code(code)
            for code in getattr(self._cfg, "blocked_market_warning_codes", ["2", "3"])
        }
        if self._cfg.block_investment_warning and (
            self._normalize_status_code(mrkt_warn_cls_code) in blocked_market_warning_codes
            or self._normalize_status_code(iscd_stat_cls_code) in blocked_status_codes
        ):
            return self._blocked(
                "investment_warning_stock",
                "투자경고/위험 또는 거래정지 상태 종목은 주문할 수 없습니다.",
                blocked_stock_status_codes=sorted(blocked_status_codes),
                blocked_market_warning_codes=sorted(blocked_market_warning_codes),
                **context,
            )

        if self._cfg.block_investment_caution and invt_caful_yn == "Y":
            return self._blocked(
                "investment_caution_stock",
                "투자주의 종목은 주문할 수 없습니다.",
                **context,
            )

        if self._cfg.min_market_cap_won > 0:
            if market_cap <= 0:
                return self._blocked(
                    "market_cap_unavailable",
                    "시가총액 데이터가 없어 소형주 검증을 통과할 수 없습니다.",
                    min_market_cap_won=self._cfg.min_market_cap_won,
                    **context,
                )
            if market_cap < self._cfg.min_market_cap_won:
                return self._blocked(
                    "market_cap_too_low",
                    "시가총액이 최소 기준보다 작습니다.",
                    min_market_cap_won=self._cfg.min_market_cap_won,
                    **context,
                )

        return OrderPolicyDecision(
            allowed=True,
            rule="security_status",
            reason="security_status_allowed",
            severity="pass",
            context=context,
        )

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
        if side == OrderSide.BUY and not self._effective_allow_market_buy():
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
        total_bid_qty = self._to_int(self._pick(quote, "total_bidp_rsqn", "총매수호가잔량"))
        if total_bid_qty <= 0:
            total_bid_qty = sum(
                self._to_int(self._pick(quote, f"bidp_rsqn{i}", f"매수호가잔량{i}"))
                for i in range(1, 11)
            )
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
        effective_max_spread_pct = self._effective_max_spread_pct()
        if spread_pct > effective_max_spread_pct:
            return self._blocked(
                "spread_too_wide",
                "스프레드가 허용 범위를 초과했습니다.",
                stock_code=stock_code,
                ask=ask,
                bid=bid,
                spread_pct=round(spread_pct, 3),
                max_spread_pct=effective_max_spread_pct,
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
        effective_max_market_slippage_pct = self._effective_max_market_slippage_pct()
        if order_type == "market" and slippage_pct > effective_max_market_slippage_pct:
            return self._blocked(
                "market_slippage_too_high",
                "시장가 예상 슬리피지가 허용 범위를 초과했습니다.",
                stock_code=stock_code,
                side=side.value,
                executable_price=executable_price,
                reference_price=reference_price,
                slippage_pct=round(slippage_pct, 3),
                max_market_slippage_pct=effective_max_market_slippage_pct,
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

        top_of_book_qty = ask_qty if side == OrderSide.BUY else bid_qty
        book_qty = top_of_book_qty
        if side == OrderSide.SELL and total_bid_qty > 0:
            book_qty = total_bid_qty
        if book_qty > 0 and qty > book_qty:
            return self._blocked(
                "top_of_book_qty_short",
                "최우선 호가 잔량보다 주문 수량이 큽니다.",
                stock_code=stock_code,
                side=side.value,
                qty=qty,
                top_of_book_qty=top_of_book_qty,
                available_book_qty=book_qty,
            )
        effective_max_top_participation_pct = self._effective_max_top_of_book_participation_pct()
        if book_qty > 0 and effective_max_top_participation_pct > 0:
            participation_pct = qty / book_qty * 100
            if participation_pct > effective_max_top_participation_pct:
                return self._blocked(
                    "top_of_book_participation_too_high",
                    "주문 수량이 최우선 호가 잔량 대비 허용 비율을 초과합니다.",
                    stock_code=stock_code,
                    side=side.value,
                    qty=qty,
                    top_of_book_qty=top_of_book_qty,
                    available_book_qty=book_qty,
                    participation_pct=round(participation_pct, 3),
                    max_top_of_book_participation_pct=effective_max_top_participation_pct,
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
                "top_of_book_qty": top_of_book_qty,
                "available_book_qty": book_qty,
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

    def _security_status_failure_decision(self, stock_code: str, reason: str) -> OrderPolicyDecision:
        if self._cfg.security_status_fail_policy == "block":
            return self._blocked(
                "security_status_unavailable",
                "종목 상태 조회 실패로 주문을 검증할 수 없습니다.",
                stock_code=stock_code,
                security_status_error=reason,
            )
        self._logger.warning(
            f"[OrderPolicy][CHECK_ERROR] rule=security_status_unavailable "
            f"stock_code={stock_code} reason={reason}"
        )
        return OrderPolicyDecision(
            allowed=True,
            rule="security_status_unavailable",
            reason="security_status_check_fail_open",
            severity="pass",
            context={"stock_code": stock_code, "security_status_error": reason},
        )

    def _trade_flow_failure_decision(self, stock_code: str, reason: str) -> OrderPolicyDecision:
        if self._cfg.trade_flow_fail_policy == "block":
            return self._blocked(
                "trade_flow_unavailable",
                "체결 흐름 조회 실패로 주문을 검증할 수 없습니다.",
                stock_code=stock_code,
                trade_flow_error=reason,
            )
        self._logger.warning(
            f"[OrderPolicy][CHECK_ERROR] rule=trade_flow_unavailable "
            f"stock_code={stock_code} reason={reason}"
        )
        return OrderPolicyDecision(
            allowed=True,
            rule="trade_flow_unavailable",
            reason="trade_flow_check_fail_open",
            severity="pass",
            context={"stock_code": stock_code, "trade_flow_error": reason},
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
    def _extract_stock_output(data) -> dict:
        if isinstance(data, dict):
            output = data.get("output")
            if hasattr(output, "to_dict"):
                return output.to_dict()
            if isinstance(output, dict):
                return output
            return data
        if hasattr(data, "to_dict"):
            return data.to_dict()
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

    @staticmethod
    def _is_flagged_code(value: str) -> bool:
        text = str(value or "").strip().upper()
        return text not in ("", "0", "00", "000", "N", "NONE", "NULL")

    @staticmethod
    def _normalize_status_code(value: str) -> str:
        text = str(value or "").strip().upper()
        if text.isdigit():
            return str(int(text))
        return text

    @staticmethod
    def _with_context(decision: OrderPolicyDecision, extra_context: dict[str, Any]) -> OrderPolicyDecision:
        if not extra_context:
            return decision
        merged = dict(decision.context)
        for key, value in extra_context.items():
            merged.setdefault(key, value)
        decision.context = merged
        return decision

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
