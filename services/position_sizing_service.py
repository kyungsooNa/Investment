"""포지션 사이징 서비스 — Fixed Fractional (Penbold 식).

계좌 노출 제어:
  1. risk_qty  : (총자산 × risk_pct) / 1주당 리스크(KRW)
  2. cap_qty   : 단일 종목 비중 상한 잔여분
  3. cash_qty  : 매수 가능 현금 한도
  4. alloc_qty : 전략별 자본 할당 캡 (설정된 경우)
  5. signal.qty: 전략이 설정한 자발적 상한 (Optional — None 이면 제약 없음)
  final_qty = max(0, min([risk_qty, cap_qty, cash_qty, (alloc_qty), (signal.qty)]))
"""

import math
import inspect
import logging
from typing import Any, Optional, Protocol, Tuple, TYPE_CHECKING

from common.types import ErrorCode, Exchange, ResCommonResponse, TradeSignal
from core.account_snapshot import AccountSnapshotCache

if TYPE_CHECKING:
    from services.indicator_service import IndicatorService
    from config.config_loader import OrderPolicyConfig, PositionSizingConfig, RiskGateConfig


class QuoteProvider(Protocol):
    async def get_asking_price(
        self,
        stock_code: str,
        exchange: Exchange = Exchange.KRX,
    ) -> ResCommonResponse:
        ...


class PositionSizingService:
    """장중 전략 시그널의 BUY qty 를 변동성/계좌 한도에 맞게 보정한다."""

    def __init__(
        self,
        account_snapshot_cache: AccountSnapshotCache,
        indicator_service: "IndicatorService",
        config: "PositionSizingConfig",
        logger: Optional[logging.Logger] = None,
        risk_gate_config: Optional["RiskGateConfig"] = None,
        quote_provider: Optional[QuoteProvider] = None,
        order_policy_config: Optional["OrderPolicyConfig"] = None,
    ):
        self._cache = account_snapshot_cache
        self._indicator = indicator_service
        self._cfg = config
        self._logger = logger or logging.getLogger(__name__)
        self._risk_gate_config = risk_gate_config
        self._quote_provider = quote_provider
        self._order_policy_config = order_policy_config

    # ── 공개 API ──────────────────────────────────────────────────

    async def adjust_buy_qty(
        self,
        signal: TradeSignal,
        exchange: "Exchange | None" = None,
    ) -> Tuple[int, str]:
        """BUY 시그널 수량을 포지션 사이징 규칙에 맞게 조정한다.

        Returns:
            (final_qty, reason)  — final_qty == 0 이면 주문 skip.
        """
        if not self._cfg.enabled:
            if signal.qty is None:
                return 0, "sizing_disabled"
            return signal.qty, "sizing_disabled"

        if signal.action != "BUY" or signal.price <= 0:
            return signal.qty, "bypass"
        if signal.qty is not None and signal.qty <= 0:
            return signal.qty, "bypass"

        price = signal.price

        # 1. 잔고 스냅샷 (API 비호출, 캐시에서 읽기)
        snapshot = await self._cache.get(exchange)
        total_equity = snapshot.total_equity
        available_cash = snapshot.available_cash
        current_position_value = snapshot.positions.get(signal.code, 0)

        if total_equity <= 0:
            self._logger.warning(
                f"[PositionSizing] {signal.code}: total_equity=0, 주문 skip"
            )
            return 0, "risk_zero"

        # 2. 1주당 리스크(KRW) 산정
        per_share_risk = await self._get_per_share_risk_krw(signal, price)

        # 3. risk_qty (Penbold Fixed Fractional)
        total_risk_krw = total_equity * self._cfg.per_trade_risk_pct / 100
        risk_qty = math.floor(total_risk_krw / per_share_risk) if per_share_risk > 0 else 0

        # 4. cap_qty (단일 종목 비중 상한 — 기존 보유분 차감)
        weight_budget = max(
            total_equity * self._cfg.max_per_position_pct / 100 - current_position_value,
            0,
        )
        cap_qty = math.floor(weight_budget / price) if price > 0 else 0

        # 5. cash_qty (매수 가능 현금)
        cash_qty = math.floor(available_cash / price) if price > 0 else 0

        # 6. alloc_qty (전략별 자본 할당 캡, None 이면 제약 없음)
        alloc_qty = self._calc_strategy_alloc_qty(signal, price, total_equity)

        # 7. 주문 직전 hard-block 정책과 같은 한도도 미리 수량에 반영
        max_order_amount_qty = self._calc_max_order_amount_qty(price)
        top_of_book_qty = await self._calc_top_of_book_qty(signal, price, exchange)

        # 8. 후보 목록 구성 — signal.qty / alloc_qty 는 None 이면 제외 (제약 없음)
        candidates = {
            "risk_limited": risk_qty,
            "cap_limited": cap_qty,
            "cash_limited": cash_qty,
        }
        if alloc_qty is not None:
            candidates["strategy_capital_cap"] = alloc_qty
        if max_order_amount_qty is not None:
            candidates["max_order_amount_limited"] = max_order_amount_qty
        if top_of_book_qty is not None:
            candidates["top_of_book_limited"] = top_of_book_qty
        if signal.qty is not None:
            candidates["signal_cap"] = signal.qty
        final_qty = max(0, min(candidates.values()))

        # 9. 사유 결정
        if final_qty == 0:
            if risk_qty == 0:
                reason = "risk_zero"
            elif cap_qty == 0:
                reason = "cap_exhausted"
            elif alloc_qty is not None and alloc_qty == 0:
                reason = "strategy_capital_cap"
            elif max_order_amount_qty is not None and max_order_amount_qty == 0:
                reason = "order_amount_cap"
            elif top_of_book_qty is not None and top_of_book_qty == 0:
                reason = "top_of_book_limited"
            else:
                reason = "cash_short"
        elif signal.qty is not None and final_qty == signal.qty:
            reason = "ok"
        else:
            # signal.qty 상한보다 작게 됐거나, qty=None 에서 sizing 이 단독 결정
            reason_priority = (
                "strategy_capital_cap",
                "max_order_amount_limited",
                "top_of_book_limited",
                "cash_limited",
                "cap_limited",
                "risk_limited",
            )
            reason = next(
                key for key in reason_priority
                if candidates.get(key) == final_qty
            )

        self._logger.info(
            f"[PositionSizing] {signal.code} price={price:,} "
            f"equity={total_equity:,} risk/share={per_share_risk:.0f} "
            f"risk_qty={risk_qty} cap_qty={cap_qty} cash_qty={cash_qty} alloc_qty={alloc_qty} "
            f"max_order_amount_qty={max_order_amount_qty} top_of_book_qty={top_of_book_qty} "
            f"signal_qty={signal.qty} → final={final_qty} ({reason})"
        )
        return final_qty, reason

    # ── 내부 ──────────────────────────────────────────────────────

    def _calc_strategy_alloc_qty(self, signal: TradeSignal, price: int, total_equity: int) -> Optional[int]:
        """전략별 자본 할당 캡 (capital_allocation_pct) 기반 최대 수량.

        캡 미설정 시 None 반환 (제약 없음).
        """
        if not self._risk_gate_config or not signal.strategy_name or price <= 0:
            return None

        cfg = self._risk_gate_config
        limit = cfg.strategy_limits.get(signal.strategy_name) or cfg.default_strategy_limit
        cap_pct = limit.capital_allocation_pct if limit else None
        if cap_pct is None:
            return None

        alloc_budget = int(total_equity * cap_pct / 100)
        return math.floor(alloc_budget / price)

    def _calc_max_order_amount_qty(self, price: int) -> Optional[int]:
        if not self._risk_gate_config or price <= 0:
            return None
        max_amount = int(getattr(self._risk_gate_config, "max_order_amount_won", 0) or 0)
        if max_amount <= 0:
            return None
        return math.floor(max_amount / price)

    async def _calc_top_of_book_qty(
        self,
        signal: TradeSignal,
        price: int,
        exchange: "Exchange | None",
    ) -> Optional[int]:
        if self._quote_provider is None or price <= 0:
            return None
        if self._order_policy_config is not None and not getattr(
            self._order_policy_config, "order_book_checks_enabled", True
        ):
            return None

        try:
            resp = self._quote_provider.get_asking_price(
                signal.code,
                exchange=exchange or Exchange.KRX,
            )
            if inspect.isawaitable(resp):
                resp = await resp
        except TypeError:
            try:
                resp = self._quote_provider.get_asking_price(signal.code)
                if inspect.isawaitable(resp):
                    resp = await resp
            except Exception as exc:
                self._logger.debug(f"[PositionSizing] 호가 조회 실패 ({signal.code}): {exc}")
                return None
        except Exception as exc:
            self._logger.debug(f"[PositionSizing] 호가 조회 실패 ({signal.code}): {exc}")
            return None

        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            return None

        quote = self._extract_quote_data(resp.data)
        ask_qty = self._to_int(self._pick(quote, "askp_rsqn1", "매도호가잔량1"))
        if ask_qty <= 0:
            return None

        max_pct = 100.0
        if self._order_policy_config is not None:
            max_pct = float(getattr(
                self._order_policy_config,
                "max_top_of_book_participation_pct",
                100.0,
            ) or 0.0)
        if max_pct > 0:
            return math.floor(ask_qty * max_pct / 100)
        return ask_qty

    async def _get_per_share_risk_krw(self, signal: TradeSignal, price: int) -> float:
        """1주당 리스크 금액(KRW) — ATR 기반 또는 stop_loss_pct 기반."""
        stop_pct = abs(signal.stop_loss_pct or self._cfg.default_stop_loss_pct)
        stop_from_pct = price * stop_pct / 100  # KRW

        atr_krw = await self._get_atr_krw(signal)
        atr_mult = signal.atr_multiplier or self._cfg.atr_multiplier
        stop_from_atr = atr_krw * atr_mult if atr_krw > 0 else 0.0

        min_stop = price * self._cfg.min_stop_distance_pct / 100  # 하한 보호

        return max(stop_from_pct, stop_from_atr, min_stop)

    async def _get_atr_krw(self, signal: TradeSignal) -> float:
        """ATR 마지막 값을 KRW 로 반환. 실패 시 0.0."""
        try:
            resp = await self._indicator.calculate_atr(
                signal.code, period=self._cfg.atr_period
            )
            from common.types import ErrorCode
            if resp is None or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                return 0.0
            last = resp.data[-1] if isinstance(resp.data, list) else resp.data
            atr_val = last.get("atr") if isinstance(last, dict) else getattr(last, "atr", None)
            return float(atr_val) if atr_val is not None else 0.0
        except Exception as e:
            self._logger.debug(f"[PositionSizing] ATR 조회 실패 ({signal.code}): {e}")
            return 0.0

    @staticmethod
    def _extract_quote_data(data: Any) -> dict:
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
