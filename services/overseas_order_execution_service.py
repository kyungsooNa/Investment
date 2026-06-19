# services/overseas_order_execution_service.py
"""해외 VBO 게이팅 주문 실행 서비스 (Phase 4).

sized 신호(수량 산출 완료) → 지정가 매수/매도 주문 경로. 사이징은
`OverseasPositionSizingService` 가 별도로 담당하며, 본 서비스는 이미 산출된 qty 를
받아 주문만 낸다(단일 책임).

**핵심 안전 계약 — 구조적 실주문 잠금:**
`live_enabled=False`(기본)에서는 broker 주문 메서드를 **절대 호출하지 않고** would-be
주문 레코드만 반환한다(`signal_source="overseas_paper"`). `live_enabled=True` 일 때만
실호출한다. 해외 주문 TR 은 실전(모의 없음)만 존재하므로, dry-run 검증 + Phase 5
canary/kill-switch/reconcile 가 이 플래그를 켜는 유일한 주체다.

스케줄러/factory 배선(자동 발사)은 Phase 5 소관 — 본 서비스는 테스트된 게이팅
컴포넌트로만 제공된다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common.overseas_types import OverseasExchange, OverseasOrderReport
from common.types import ErrorCode, ResCommonResponse


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
        logger: Optional[logging.Logger] = None,
    ) -> None:
        # broker 는 live_enabled=True 일 때만 필요. paper 모드에선 None 허용(구조적 잠금).
        self._broker = broker
        self._live_enabled = bool(live_enabled)
        self._default_exchange = default_exchange
        self._journal = journal
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
            resp = await self._broker.place_overseas_limit_order(
                symbol=symbol, exchange=ex, side=side, qty=int(qty), limit_price=limit_str,
            )

        self._record_journal(symbol, ex, side, int(qty), limit_str, signal, exit_reason, resp)
        return resp

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
                strategy_name="LarryWilliamsVBO_overseas",
                code=symbol,
                signal=order,
                snapshot={"exchange": ex.value},
                signal_source=source,
            )
        except Exception as e:  # 저널 실패가 주문 결과를 가리지 않도록 흡수
            self._logger.warning({"event": "overseas_order_journal_error", "error": str(e)})

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
