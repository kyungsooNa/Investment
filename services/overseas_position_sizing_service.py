# services/overseas_position_sizing_service.py
"""해외 VBO canary 포지션 사이징 (Phase 4 — 고정 USD 슬롯).

**실주문 fire 경로 없음(순수 계산).** dry-run 검증 전 실주문 배선 금지 제약에 따라
본 서비스는 broker/order_execution 의존을 갖지 않으며 수량 산출만 한다.

사이징 모델: 고정 USD 캐너리 슬롯 ÷ 지정가(USD) = 수량(floor).
환율(USD/KRW)은 KRW 환산 노출 리포팅용 부가값이며 사이징 자체엔 불필요하다
(USD 슬롯을 USD 가격으로 나누므로). FX 는 KIS 잔고 응답에서 추출한다.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

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
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if slot_usd <= 0:
            raise ValueError("slot_usd must be positive")
        if max_qty is not None and max_qty <= 0:
            raise ValueError("max_qty must be positive when provided")
        self._slot_usd = float(slot_usd)
        self._max_qty = max_qty
        self._logger = logger or logging.getLogger(__name__)

    def size(
        self,
        *,
        limit_price_usd: float,
        available_usd: Optional[float] = None,
        fx_krw_per_usd: Optional[float] = None,
    ) -> dict:
        """지정가(USD)에 대해 고정 슬롯 기준 매수 수량을 산출한다.

        반환: {qty, limit_price_usd, notional_usd, slot_usd,
               fx_krw_per_usd, krw_exposure, reason}
        """
        price = _to_float(limit_price_usd)
        if price <= 0:
            return self._result(0, 0.0, fx_krw_per_usd, "invalid_price")

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
                return self._result(0, price, fx_krw_per_usd, "insufficient_usd")

        return self._result(qty, price, fx_krw_per_usd, reason)

    def _result(
        self,
        qty: int,
        price: float,
        fx: Optional[float],
        reason: str,
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
        }
