# services/overseas_order_execution_service.py
"""해외 VBO 게이팅 주문 실행 서비스 (Phase 4).

sized 신호(수량 산출 완료) → 지정가 매수/매도 주문 경로. 사이징은
`OverseasPositionSizingService` 가 별도로 담당하며, 본 서비스는 이미 산출된 qty 를
받아 주문만 낸다(단일 책임).

**핵심 안전 계약 — 구조적 실주문 잠금:**
`live_enabled=False`(기본)에서는 broker 주문 메서드를 **절대 호출하지 않고** would-be
주문 레코드만 반환한다(`signal_source="overseas_paper"`). `live_enabled=True` 일 때만
실호출한다. 이 플래그를 켜는 유일한 주체는 dry-run 검증 + Phase 5
canary/kill-switch/reconcile 다.

주: 과거 이 자리에 있던 "해외 주문 TR 은 실전(모의 없음)만 존재" 기술은 stale 이다.
#606 에서 모의 미지원인 주간거래(TTTS603x) → 정규장(TTTT100xU) 으로 전환하며
`tr_ids_config.yaml` 에 VTTT... 모의 쌍이 추가됐고 `trid_provider` 가
`is_paper_trading` 으로 분기한다. 다만 모의 서버가 해외 주문을 실제로 수락하는지는
미검증 — `scripts/probe_overseas_paper_order.py` 참고.

스케줄러/factory 배선(자동 발사)은 Phase 5 소관 — 본 서비스는 테스트된 게이팅
컴포넌트로만 제공된다.

웹 수동 주문 라우트(`POST /api/overseas/order`)도 kill-switch/저널 게이팅을 얻기 위해
이 서비스를 재사용한다(`live_enabled=True` 전용 인스턴스). 자동 전략 경로는 별도
인스턴스라 `live_enabled=False` 잠금이 그대로 유지된다. 저널 기록이 섞이지 않도록
`journal_strategy_name` 으로 경로를 구분한다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse
from services.notification_service import NotificationCategory, NotificationLevel


class OverseasOrderExecutionService:
    SIGNAL_SOURCE_LIVE = "overseas_live"
    SIGNAL_SOURCE_PAPER = "overseas_paper"

    def __init__(
        self,
        broker,
        *,
        live_enabled: bool = False,
        default_exchange: OverseasExchange = OverseasExchange.NASD,
        journal=None,
        kill_switch=None,
        risk_gate=None,
        open_position_count_provider=None,
        notification_service=None,
        journal_strategy_name: str = "LarryWilliamsVBO_overseas",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        # broker 는 live_enabled=True 일 때만 필요. paper 모드에선 None 허용(구조적 잠금).
        self._broker = broker
        self._live_enabled = bool(live_enabled)
        self._default_exchange = default_exchange
        self._journal = journal
        self._notification_service = notification_service
        # 해외 전용 리스크 게이트(USD→KRW 환산 후 RiskGateConfig 한도 적용).
        # kill_switch 와 마찬가지로 live 실주문 직전에만 적용된다.
        self._risk_gate = risk_gate
        self._open_position_count_provider = open_position_count_provider
        # 저널 상 경로 구분(자동 VBO / 수동 주문). 소비 측이 섞어 읽지 않도록 한다.
        self._journal_strategy_name = journal_strategy_name
        # live 실주문 직전 차단 게이트(check_orders_allowed). paper 모드는 실주문이 없어 미적용.
        self._kill_switch = kill_switch
        self._logger = logger or logging.getLogger(__name__)

    async def place_entry(
        self,
        *,
        code: str,
        qty: int,
        limit_price: float,
        exchange: Optional[OverseasExchange] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> ResCommonResponse:
        """지정가 매수 주문(게이팅). live_enabled=False 면 would-be 만 반환."""
        return await self._place(
            code=code, qty=qty, limit_price=limit_price, side="buy",
            exchange=exchange, signal=signal, exit_reason=None,
        )

    async def place_exit(
        self,
        *,
        code: str,
        qty: int,
        limit_price: float,
        reason: str = "",
        exchange: Optional[OverseasExchange] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> ResCommonResponse:
        """지정가 매도(청산) 주문(게이팅). live_enabled=False 면 would-be 만 반환."""
        return await self._place(
            code=code, qty=qty, limit_price=limit_price, side="sell",
            exchange=exchange, signal=signal, exit_reason=reason,
        )

    async def _place(
        self,
        *,
        code: str,
        qty: int,
        limit_price: float,
        side: str,
        exchange: Optional[OverseasExchange],
        signal: Optional[Dict[str, Any]],
        exit_reason: Optional[str],
    ) -> ResCommonResponse:
        symbol = str(code).upper()
        ex = exchange or self._default_exchange
        # 어떤 모드에서도 잘못된 입력은 broker 도달 전 차단.
        if int(qty) <= 0:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="주문수량은 0보다 커야 합니다.", data=None,
            )
        if self._to_float(limit_price) <= 0:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="지정가는 0보다 커야 합니다(해외는 지정가만 지원).", data=None,
            )
        limit_str = self._price_str(limit_price)

        if not self._live_enabled:
            resp = self._would_be_response(symbol, ex, side, int(qty), limit_str, exit_reason)
        else:
            blocked = await self._kill_switch_block(symbol, side)
            if blocked is not None:
                return blocked
            blocked = await self._risk_gate_block(symbol, side, int(qty), limit_price)
            if blocked is not None:
                return blocked
            resp = await self._broker.place_overseas_limit_order(
                symbol=symbol, exchange=ex, side=side, qty=int(qty), limit_price=limit_str,
            )

        self._record_journal(symbol, ex, side, int(qty), limit_str, signal, exit_reason, resp)
        await self._emit_order_notification(symbol, ex, side, int(qty), limit_str, signal, exit_reason, resp)
        return resp

    async def _risk_gate_block(
        self, symbol: str, side: str, qty: int, limit_price: float,
    ) -> Optional[ResCommonResponse]:
        """live 실주문 직전 리스크 게이트. 차단이면 응답 반환, 통과면 None.

        매도는 게이트 내부에서 항상 통과된다 — 청산을 막으면 포지션이 갇힌다.
        """
        if self._risk_gate is None:
            return None
        open_count = 0
        if self._open_position_count_provider is not None:
            try:
                open_count = int(self._open_position_count_provider() or 0)
            except Exception as e:
                self._logger.warning({"event": "overseas_open_position_count_error",
                                      "code": symbol, "error": str(e)})
                open_count = 0
        return await self._risk_gate.validate_order(
            symbol=symbol, side=side, qty=qty, limit_price_usd=self._to_float(limit_price),
            open_position_count=open_count,
            strategy_name=self._journal_strategy_name,
        )

    async def _kill_switch_block(self, symbol: str, side: str) -> Optional[ResCommonResponse]:
        """live 실주문 직전 kill-switch 차단 확인. 차단이면 응답 반환, 통과면 None."""
        if self._kill_switch is None:
            return None
        allowed, reason = await self._kill_switch.check_orders_allowed()
        if allowed:
            return None
        self._logger.warning({
            "event": "overseas_order_kill_switch_blocked", "code": symbol,
            "side": side, "reason": reason,
        })
        return ResCommonResponse(
            rt_cd=ErrorCode.KILL_SWITCH_BLOCKED.value,
            msg1=f"Kill Switch 활성 — {reason or ''}", data=None,
        )

    def _would_be_response(
        self, symbol: str, ex: OverseasExchange, side: str, qty: int,
        limit_str: str, exit_reason: Optional[str],
    ) -> ResCommonResponse:
        raw: Dict[str, Any] = {"would_be": True, "signal_source": self.SIGNAL_SOURCE_PAPER}
        if exit_reason:
            raw["exit_reason"] = exit_reason
        report = OverseasOrderReport(
            symbol=symbol, exchange=ex, side=side, qty=qty,
            limit_price=limit_str, broker_order_no="", raw=raw,
        )
        self._logger.info({
            "event": "overseas_order_would_be", "code": symbol, "side": side,
            "qty": qty, "limit_price": limit_str, "exchange": ex.value,
        })
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="would-be (live_enabled=False)", data=report,
        )

    def _record_journal(
        self, symbol: str, ex: OverseasExchange, side: str, qty: int,
        limit_str: str, signal: Optional[Dict[str, Any]], exit_reason: Optional[str],
        resp: ResCommonResponse,
    ) -> None:
        if self._journal is None:
            return
        source = self.SIGNAL_SOURCE_LIVE if self._live_enabled else self.SIGNAL_SOURCE_PAPER
        order = {
            "code": symbol, "side": side, "qty": qty, "limit_price": limit_str,
            "rt_cd": getattr(resp, "rt_cd", None),
        }
        if exit_reason:
            order["exit_reason"] = exit_reason
        if signal:
            order["signal"] = signal
        try:
            self._journal.record(
                strategy_name=self._journal_strategy_name,
                code=symbol,
                signal=order,
                snapshot={"exchange": ex.value},
                signal_source=source,
            )
        except Exception as e:  # 저널 실패가 주문 결과를 가리지 않도록 흡수
            self._logger.warning({"event": "overseas_order_journal_error", "error": str(e)})

    async def _emit_order_notification(
        self, symbol: str, ex: OverseasExchange, side: str, qty: int,
        limit_str: str, signal: Optional[Dict[str, Any]], exit_reason: Optional[str],
        resp: ResCommonResponse,
    ) -> None:
        if self._notification_service is None:
            return
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return

        signal = signal or {}
        source = self.SIGNAL_SOURCE_LIVE if self._live_enabled else self.SIGNAL_SOURCE_PAPER
        action = str(signal.get("action") or side).upper()
        strategy = str(signal.get("strategy") or self._journal_strategy_name)
        title = f"미국장 VBO {action} {symbol}"
        mode_label = "live" if self._live_enabled else "paper"
        message = f"{symbol} {side.upper()} {qty}주 @ {limit_str} ({ex.value}, {mode_label})"
        if exit_reason:
            message += f"\n청산 사유: {exit_reason}"
        reason = signal.get("reason")
        if reason:
            message += f"\n사유: {reason}"

        metadata: Dict[str, Any] = {
            "market": "overseas_us",
            "strategy": strategy,
            "code": symbol,
            "exchange": ex.value,
            "side": side,
            "qty": qty,
            "limit_price": limit_str,
            "signal_source": source,
            "force_external": True,
        }
        if exit_reason:
            metadata["exit_reason"] = exit_reason
        if signal.get("realized_pct") is not None:
            metadata["return_rate"] = self._to_float(signal.get("realized_pct"))

        try:
            await self._notification_service.emit(
                NotificationCategory.STRATEGY,
                NotificationLevel.WARNING,
                title,
                message,
                metadata=metadata,
            )
        except Exception as e:
            self._logger.warning({"event": "overseas_order_notification_error", "error": str(e)})

    @staticmethod
    def decide_daily_exit(
        *,
        entry_price: float,
        stop_price: float,
        daily_bar: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """보유 포지션을 일봉 한 개에 대해 청산 판정(순수 로직).

        dry-run / 백테스트 모델과 동일: 당일저 <= 손절가면 손절가 청산("stop"),
        아니면 종가 청산("eod"). 유효 종가/저가가 없으면 None(판정 보류).
        반환: {"exit_price": float, "exit_reason": "stop"|"eod"} | None
        """
        low = OverseasOrderExecutionService._to_float(daily_bar.get("low"))
        close = OverseasOrderExecutionService._to_float(daily_bar.get("close"))
        if close <= 0:
            return None
        stop = OverseasOrderExecutionService._to_float(stop_price)
        if low > 0 and stop > 0 and low <= stop:
            return {"exit_price": stop, "exit_reason": "stop"}
        return {"exit_price": close, "exit_reason": "eod"}

    @staticmethod
    def _to_float(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _price_str(x) -> str:
        # 정수 가격은 정수 문자열, 그 외는 원본 float 문자열(불필요한 .0 회피).
        f = OverseasOrderExecutionService._to_float(x)
        if f == int(f):
            return str(int(f)) if not isinstance(x, float) else str(f)
        return str(f)
