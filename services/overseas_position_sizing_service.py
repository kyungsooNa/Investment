# services/overseas_position_sizing_service.py
"""해외 포지션 사이징 (고정 USD 슬롯 + 리스크 기반).

**실주문 fire 경로 없음(순수 계산).** broker/order_execution 의존을 갖지 않는다.

두 가지 모델을 지원한다.

1. **리스크 기반** (총자산과 손절가를 아는 경우) — 국내 `PositionSizingService` 와
   같은 규칙: 1주당 리스크 예산 ÷ 손절 거리. 고정 슬롯은 손절이 -2% 든 -10% 든
   같은 수량을 사서 주당 리스크가 손절 폭에 따라 요동친다.
   `operating_profile` 에 따라 canary/real_limited overlay 를 적용한다.
2. **고정 USD 슬롯** (폴백) — 슬롯 ÷ 지정가. dry-run 경로는 총자산을 모르므로
   폴백이 없으면 관측 신호가 통째로 사라진다.

환율(USD/KRW)은 KRW 환산 노출 리포팅용 부가값이다. FX 는 KIS 잔고 응답에서 추출한다.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional

from config.config_loader import PositionSizingConfig

# KIS 해외 잔고/현재잔고 응답의 환율 후보 필드.
# 공식 표본이 부족해 표본별로 키가 갈리므로 다중 후보 탐색 — 실 fixture 확보 시 단일화.
_FX_RATE_KEYS = ("frst_bltn_exrt", "bass_exrt", "ovrs_excg_exrt", "exrt")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_fx_krw_per_usd(balance_data: Any) -> Optional[float]:
    """KIS 잔고 응답(raw dict)에서 USD/KRW 환율을 관용적으로 추출한다.

    output1(보유종목 list)·output2(요약 dict 또는 list)를 후보 키로 탐색해
    첫 양수 환율을 반환한다. 없거나 비양수면 None(→ KRW 환산 생략).
    """
    if not isinstance(balance_data, dict):
        return None
    sections: list[dict] = []
    for key in ("output1", "output2"):
        sec = balance_data.get(key)
        if isinstance(sec, list):
            sections.extend(s for s in sec if isinstance(s, dict))
        elif isinstance(sec, dict):
            sections.append(sec)
    for sec in sections:
        for fx_key in _FX_RATE_KEYS:
            if fx_key in sec:
                rate = _to_float(sec.get(fx_key))
                if rate > 0:
                    return rate
    return None


class OverseasPositionSizingService:
    """고정 USD 슬롯 기반 해외 캐너리 사이징 (실주문 경로 없음)."""

    def __init__(
        self,
        *,
        slot_usd: float,
        max_qty: Optional[int] = None,
        sizing_config: Optional[PositionSizingConfig] = None,
        operating_profile: str = "canary",
        is_real_mode_provider: Optional[Callable[[], bool]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if slot_usd <= 0:
            raise ValueError("slot_usd must be positive")
        if max_qty is not None and max_qty <= 0:
            raise ValueError("max_qty must be positive when provided")
        self._slot_usd = float(slot_usd)
        self._max_qty = max_qty
        self._cfg = sizing_config or PositionSizingConfig()
        self._operating_profile = str(operating_profile or "canary")
        self._is_real_mode_provider = is_real_mode_provider
        self._logger = logger or logging.getLogger(__name__)

    # ── 프로파일 overlay ───────────────────────────────────────────────

    def _is_real_mode(self) -> bool:
        if self._is_real_mode_provider is None:
            return False
        try:
            return bool(self._is_real_mode_provider())
        except Exception:
            return False

    def _overlay(self):
        """현재 프로파일에 적용할 리스크 파라미터 소스. paper 는 base 를 쓴다."""
        if not self._is_real_mode() or self._operating_profile == "real_full":
            return self._cfg
        if self._operating_profile == "canary":
            return self._cfg.canary_overrides
        return self._cfg.real_mode_overrides

    def _per_trade_risk_pct(self) -> float:
        return _to_float(getattr(self._overlay(), "per_trade_risk_pct",
                                 self._cfg.per_trade_risk_pct))

    def _max_per_position_pct(self) -> float:
        return _to_float(getattr(self._overlay(), "max_per_position_pct",
                                 self._cfg.max_per_position_pct))

    def size(
        self,
        *,
        limit_price_usd: float,
        available_usd: Optional[float] = None,
        fx_krw_per_usd: Optional[float] = None,
        stop_price_usd: Optional[float] = None,
        account_equity_usd: Optional[float] = None,
    ) -> dict:
        """지정가(USD)에 대해 고정 슬롯 기준 매수 수량을 산출한다.

        반환: {qty, limit_price_usd, notional_usd, slot_usd,
               fx_krw_per_usd, krw_exposure, reason}
        """
        price = _to_float(limit_price_usd)
        if price <= 0:
            return self._result(0, 0.0, fx_krw_per_usd, "invalid_price")

        risk = self._risk_based_qty(price, stop_price_usd, account_equity_usd)
        if risk is not None:
            qty, reason, risk_amount = risk
            if qty < 1:
                return self._result(0, price, fx_krw_per_usd, reason,
                                    risk_amount_usd=risk_amount)
        else:
            # 총자산이나 손절가를 모르면 고정 슬롯으로 폴백한다(dry-run 경로 호환).
            risk_amount = None
            qty = math.floor(self._slot_usd / price)
            if qty < 1:
                return self._result(0, price, fx_krw_per_usd, "slot_too_small")
            reason = "slot"
        if self._max_qty is not None and qty > self._max_qty:
            qty = self._max_qty
            reason = "capped_by_max_qty"

        if available_usd is not None:
            affordable = math.floor(_to_float(available_usd) / price)
            if affordable < qty:
                qty = max(affordable, 0)
                reason = "capped_by_available_usd"
            if qty < 1:
                return self._result(0, price, fx_krw_per_usd, "insufficient_usd",
                                    risk_amount_usd=risk_amount)

        return self._result(qty, price, fx_krw_per_usd, reason, risk_amount_usd=risk_amount)

    def _risk_based_qty(
        self, price: float, stop_price_usd, account_equity_usd,
    ) -> Optional[tuple]:
        """리스크 기반 수량. 재료가 없으면 None(→ 고정 슬롯 폴백).

        반환: (qty, reason, risk_amount_usd)
        """
        equity = _to_float(account_equity_usd)
        stop = _to_float(stop_price_usd)
        if account_equity_usd is None or stop_price_usd is None:
            return None
        if equity <= 0 or stop <= 0 or stop >= price:
            # 손절이 진입가 위면 리스크 계산이 성립하지 않는다 — 폴백.
            return None

        # 손절이 진입가에 붙으면 분모가 0에 수렴해 수량이 발산한다.
        min_distance = price * (_to_float(self._cfg.min_stop_distance_pct) / 100.0)
        stop_distance = max(price - stop, min_distance)
        if stop_distance <= 0:
            return None

        risk_amount = equity * (self._per_trade_risk_pct() / 100.0)
        qty = math.floor(risk_amount / stop_distance)
        reason = "risk_based"

        weight_cap_usd = equity * (self._max_per_position_pct() / 100.0)
        max_by_weight = math.floor(weight_cap_usd / price)
        if max_by_weight < qty:
            qty = max_by_weight
            reason = "capped_by_position_weight"
        return max(qty, 0), reason, risk_amount

    def _result(
        self,
        qty: int,
        price: float,
        fx: Optional[float],
        reason: str,
        risk_amount_usd: Optional[float] = None,
    ) -> dict:
        notional_usd = round(qty * price, 4)
        fx_valid = fx if (fx and fx > 0) else None
        krw_exposure = (
            round(notional_usd * fx_valid, 2) if (fx_valid and qty > 0) else None
        )
        return {
            "qty": qty,
            "limit_price_usd": price,
            "notional_usd": notional_usd,
            "slot_usd": self._slot_usd,
            "fx_krw_per_usd": fx_valid,
            "krw_exposure": krw_exposure,
            "reason": reason,
            "risk_amount_usd": (round(risk_amount_usd, 4)
                                if risk_amount_usd is not None else None),
        }
