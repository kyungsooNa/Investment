"""포지션 사이징 서비스 — Fixed Fractional (Penbold 식).

계좌 노출 제어 3단계:
  1. risk_qty  : (총자산 × risk_pct) / 1주당 리스크(KRW)
  2. cap_qty   : 단일 종목 10% 비중 상한 잔여분
  3. cash_qty  : 매수 가능 현금 한도
  final_qty = max(0, min(signal.qty, risk_qty, cap_qty, cash_qty))
"""

import math
import logging
from typing import Optional, Tuple, TYPE_CHECKING

from common.types import TradeSignal
from core.account_snapshot import AccountSnapshotCache

if TYPE_CHECKING:
    from services.indicator_service import IndicatorService
    from common.types import Exchange, ErrorCode
    from config.config_loader import PositionSizingConfig, RiskGateConfig


class PositionSizingService:
    """장중 전략 시그널의 BUY qty 를 변동성/계좌 한도에 맞게 보정한다."""

    def __init__(
        self,
        account_snapshot_cache: AccountSnapshotCache,
        indicator_service: "IndicatorService",
        config: "PositionSizingConfig",
        logger: Optional[logging.Logger] = None,
        risk_gate_config: Optional["RiskGateConfig"] = None,
    ):
        self._cache = account_snapshot_cache
        self._indicator = indicator_service
        self._cfg = config
        self._logger = logger or logging.getLogger(__name__)
        self._risk_gate_config = risk_gate_config

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
            return signal.qty, "sizing_disabled"

        if signal.action != "BUY" or signal.qty <= 0 or signal.price <= 0:
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

        # 6. alloc_qty (전략별 자본 할당 캡)
        alloc_qty = self._calc_strategy_alloc_qty(signal, price, total_equity)

        # 7. 5-way min
        final_qty = max(0, min(signal.qty, risk_qty, cap_qty, cash_qty, alloc_qty))

        # 8. 사유 결정
        if final_qty == 0:
            if risk_qty == 0:
                reason = "risk_zero"
            elif cap_qty == 0:
                reason = "cap_exhausted"
            elif alloc_qty == 0:
                reason = "strategy_capital_cap"
            else:
                reason = "cash_short"
        elif final_qty < signal.qty:
            limiting = min(risk_qty, cap_qty, cash_qty, alloc_qty)
            if limiting == alloc_qty:
                reason = "strategy_capital_cap"
            elif limiting == cash_qty:
                reason = "cash_limited"
            elif limiting == cap_qty:
                reason = "cap_limited"
            else:
                reason = "risk_limited"
        else:
            reason = "ok"

        self._logger.info(
            f"[PositionSizing] {signal.code} price={price:,} "
            f"equity={total_equity:,} risk/share={per_share_risk:.0f} "
            f"risk_qty={risk_qty} cap_qty={cap_qty} cash_qty={cash_qty} alloc_qty={alloc_qty} "
            f"signal_qty={signal.qty} → final={final_qty} ({reason})"
        )
        return final_qty, reason

    # ── 내부 ──────────────────────────────────────────────────────

    def _calc_strategy_alloc_qty(self, signal: TradeSignal, price: int, total_equity: int) -> int:
        """전략별 자본 할당 캡 (capital_allocation_pct) 기반 최대 수량.

        캡 미설정 시 signal.qty 반환 (제약 없음).
        """
        if not self._risk_gate_config or not signal.strategy_name or price <= 0:
            return signal.qty

        cfg = self._risk_gate_config
        limit = cfg.strategy_limits.get(signal.strategy_name) or cfg.default_strategy_limit
        cap_pct = limit.capital_allocation_pct if limit else None
        if cap_pct is None:
            return signal.qty

        alloc_budget = int(total_equity * cap_pct / 100)
        return math.floor(alloc_budget / price)

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
