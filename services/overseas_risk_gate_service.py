"""해외 주문 리스크 게이트 (P0-3).

국내 `RiskGateService` 는 원화 한도·국내 `Exchange`·`AccountSnapshotCache` 에 결합돼
있어 해외 주문에 그대로 쓸 수 없다. USD 가격을 원화 한도와 직접 비교하면 $1,000 이
1,000원으로 읽혀 **게이트가 있으나 마나** 해지므로, 환율로 환산한 뒤 같은
`RiskGateConfig` 한도(canary/real_limited overlay 포함)를 적용한다.

원화로 환산하는 이유는 한도 단위를 맞추기 위해서만이 아니다 —
`docs/canary_procedure.md` 의 한도표가 원화 기준이고, 국내·해외를 합친 계좌 총
노출을 하나의 단위로 봐야 하기 때문이다.

설계 원칙:
  - **매도(청산)는 절대 막지 않는다.** 리스크를 줄이는 주문을 막으면 포지션이
    갇혀 손실이 커진다. kill switch 도 매도에는 적용하지 않는다(국내 force-exit 동일).
  - **환율을 모르면 매수를 막는다(fail-closed).** 한도 검증이 불가능한 상태에서
    통과시키면 게이트가 없는 것과 같다.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from common.types import ErrorCode, ResCommonResponse
from config.config_loader import RiskGateConfig


class OverseasRiskGateService:
    def __init__(
        self,
        config: Optional[RiskGateConfig] = None,
        fx_provider: Optional[Callable[[], Any]] = None,
        *,
        operating_profile: str = "canary",
        is_real_mode_provider: Optional[Callable[[], bool]] = None,
        kill_switch=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._cfg = config or RiskGateConfig()
        self._fx_provider = fx_provider
        self._operating_profile = str(operating_profile or "canary")
        self._is_real_mode_provider = is_real_mode_provider
        self._kill_switch = kill_switch
        self._logger = logger or logging.getLogger(__name__)
        self._daily_date: str = ""
        self._daily_amount_krw: float = 0.0

    # ── 한도 (프로파일 overlay) ─────────────────────────────────────────

    def _is_real_mode(self) -> bool:
        if self._is_real_mode_provider is None:
            return False
        try:
            return bool(self._is_real_mode_provider())
        except Exception:
            return False

    def _max_order_amount_won(self) -> int:
        if self._is_real_mode() and self._operating_profile == "canary":
            return self._cfg.canary_overrides.max_order_amount_won
        return self._cfg.max_order_amount_won

    def _max_open_positions(self) -> int:
        if not self._is_real_mode() or self._operating_profile == "real_full":
            return self._cfg.max_pending_orders
        if self._operating_profile == "canary":
            return self._cfg.canary_overrides.max_pending_orders
        return self._cfg.real_mode_overrides.max_pending_orders

    # ── 판정 ────────────────────────────────────────────────────────────

    async def validate_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        limit_price_usd: float,
        open_position_count: int = 0,
        strategy_name: str = "",
    ) -> Optional[ResCommonResponse]:
        """통과면 None, 차단이면 차단 응답을 반환한다."""
        if str(side).lower() != "buy":
            return None  # 청산은 막지 않는다
        if not self._cfg.enabled:
            return None

        if self._kill_switch is not None:
            blocked = await self._kill_switch_block(symbol)
            if blocked is not None:
                return blocked

        qty_i = int(qty or 0)
        price = self._f(limit_price_usd)
        if qty_i <= 0 or price <= 0:
            return self._blocked(
                "invalid_order", "주문 수량과 지정가는 0보다 커야 합니다.",
                symbol=symbol, qty=qty_i, price=price,
            )

        max_open = self._max_open_positions()
        if int(open_position_count or 0) >= max_open:
            return self._blocked(
                "max_open_positions",
                f"동시 보유 한도 초과 — 보유 {open_position_count}종 / 한도 {max_open}종",
                symbol=symbol, open_positions=open_position_count, limit=max_open,
            )

        fx = await self._resolve_fx()
        if fx is None:
            return self._blocked(
                "fx_unavailable",
                "USD/KRW 환율을 확인할 수 없어 주문 한도를 검증할 수 없습니다.",
                symbol=symbol,
            )

        notional_krw = price * qty_i * fx
        max_amount = self._max_order_amount_won()
        if notional_krw > max_amount:
            return self._blocked(
                "max_order_amount",
                f"1회 주문 금액 한도 초과 — {notional_krw:,.0f}원 / 한도 {max_amount:,}원",
                symbol=symbol, notional_krw=round(notional_krw), limit=max_amount,
            )

        daily_cap = getattr(self._cfg, "max_daily_order_amount_won", 0) or 0
        if daily_cap > 0 and self._daily_amount_krw + notional_krw > daily_cap:
            return self._blocked(
                "max_daily_order_amount",
                f"일일 누적 주문 금액 한도 초과 — "
                f"{self._daily_amount_krw + notional_krw:,.0f}원 / 한도 {daily_cap:,}원",
                symbol=symbol, daily_krw=round(self._daily_amount_krw), limit=daily_cap,
            )
        return None

    def record_filled(self, *, notional_krw: float, trade_date: str) -> None:
        """체결된 매수 금액을 일일 누계에 반영한다. 거래일이 바뀌면 누계를 초기화한다."""
        if trade_date and trade_date != self._daily_date:
            self._daily_date = trade_date
            self._daily_amount_krw = 0.0
        self._daily_amount_krw += max(self._f(notional_krw), 0.0)

    async def notional_krw(self, *, limit_price_usd: float, qty: int) -> Optional[float]:
        """주문 원화 환산액. 환율 미확인 시 None."""
        fx = await self._resolve_fx()
        if fx is None:
            return None
        return self._f(limit_price_usd) * int(qty or 0) * fx

    # ── 보조 ────────────────────────────────────────────────────────────

    async def _kill_switch_block(self, symbol: str) -> Optional[ResCommonResponse]:
        try:
            allowed, reason = await self._kill_switch.check_orders_allowed()
        except Exception as e:
            self._logger.warning({"event": "overseas_risk_gate_kill_switch_error",
                                  "code": symbol, "error": str(e)})
            return ResCommonResponse(
                rt_cd=ErrorCode.KILL_SWITCH_BLOCKED.value,
                msg1="Kill Switch 상태를 확인할 수 없습니다.", data=None,
            )
        if allowed:
            return None
        self._logger.warning({"event": "overseas_risk_gate_kill_switch_blocked",
                              "code": symbol, "reason": reason})
        return ResCommonResponse(
            rt_cd=ErrorCode.KILL_SWITCH_BLOCKED.value,
            msg1=f"Kill Switch 활성 — {reason or ''}", data=None,
        )

    async def _resolve_fx(self) -> Optional[float]:
        if self._fx_provider is None:
            return None
        try:
            rate = await self._fx_provider()
        except Exception as e:
            self._logger.warning({"event": "overseas_risk_gate_fx_error", "error": str(e)})
            return None
        rate = self._f(rate)
        return rate if rate > 0 else None

    def _blocked(self, rule: str, message: str, **detail) -> ResCommonResponse:
        self._logger.warning({"event": "overseas_risk_gate_blocked", "rule": rule, **detail})
        return ResCommonResponse(
            rt_cd=ErrorCode.RISK_GATE_BLOCKED.value, msg1=message, data=None,
        )

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
