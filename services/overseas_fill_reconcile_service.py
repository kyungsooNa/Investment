"""미국장 USD 원장 체결 대사 (Phase 2).

`OverseasTradeRepository` 는 해외에 실시간 체결 경로가 없어 *주문 접수* 시점에 기록한다.
따라서 체결되지 않은 지정가 주문도 HOLD 로 잡힌다. 이 서비스는 브로커 체결내역
(`inquire_overseas_ccnl`)을 진실로 삼아 그 과다 계상을 사후 보정한다.

**보정은 줄이는 방향만** 한다. 늘리면 브로커에 없는 보유를 만들고, 판정이 애매한 lot 을
미체결로 단정하면 실제 보유가 원장에서 사라진다 — 그래서 매칭 실패는 `unfilled` 가 아니라
`unknown` 이고 무조작이다.

`OverseasReconcileService`(잔고 drift 비교)와는 다른 축이다. 그쪽은 브로커 *잔고* 대비
포지션 drift 를 보고, 이쪽은 브로커 *체결내역* 대비 원장 lot 을 본다.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common.types import ErrorCode, ResCommonResponse

# KIS 해외 주문체결내역(TTTS3035R) 응답의 체결수량 후보 키. 표본별로 갈려서 관용 파싱한다.
_FILLED_QTY_KEYS = ("ft_ccld_qty", "ccld_qty", "tot_ccld_qty", "FT_CCLD_QTY", "CCLD_QTY")
_ORDER_NO_KEYS = ("odno", "ODNO")

VERDICT_OK = "ok"
VERDICT_PARTIAL = "partial"
VERDICT_UNFILLED = "unfilled"
VERDICT_UNKNOWN = "unknown"


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _first_key(row: Dict[str, Any], keys) -> str:
    for key in keys:
        if key in row and str(row[key]).strip() != "":
            return str(row[key]).strip()
    return ""


class OverseasFillReconcileService:
    def __init__(
        self,
        *,
        trade_repository,
        stock_query_service,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._ledger = trade_repository
        self._query = stock_query_service
        self._logger = logger or logging.getLogger(__name__)

    async def reconcile(
        self,
        *,
        start_date: str,
        end_date: str,
        exchange: str = "NASD",
        apply: bool = False,
    ) -> ResCommonResponse:
        """`exchange` 의 HOLD lot 을 브로커 체결내역과 대조한다.

        `apply=False`(기본)면 판정만 반환하고 원장은 건드리지 않는다.
        """
        exchange = str(exchange).upper()
        resp = await self._query.get_overseas_order_history(
            symbol="%",
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            side_code="00",
            ccld_nccs_dvsn="00",
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            # 조회 실패로 빈 체결내역을 받으면 전 lot 이 미체결로 보인다 — 반드시 중단한다.
            self._logger.warning(
                f"[overseas_fill_reconcile] 체결내역 조회 실패 — exchange={exchange} "
                f"msg1={getattr(resp, 'msg1', '')}"
            )
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="해외 체결내역 조회에 실패해 대사를 중단했습니다.",
                data=None,
            )

        filled_by_order = self._filled_qty_by_order(resp.data)
        holds = [h for h in self._ledger.get_holds() if str(h.get("exchange", "")).upper() == exchange]

        diffs: List[Dict[str, Any]] = []
        counts = {VERDICT_OK: 0, VERDICT_PARTIAL: 0, VERDICT_UNFILLED: 0, VERDICT_UNKNOWN: 0}
        for hold in holds:
            order_no = str(hold.get("order_no") or "").strip()
            ledger_qty = _to_int(hold.get("qty"))
            if not order_no or order_no not in filled_by_order:
                verdict, filled = VERDICT_UNKNOWN, None
            else:
                filled = filled_by_order[order_no]
                if filled <= 0:
                    verdict = VERDICT_UNFILLED
                elif filled < ledger_qty:
                    verdict = VERDICT_PARTIAL
                else:
                    verdict = VERDICT_OK

            corrected = False
            if apply and verdict == VERDICT_UNFILLED:
                corrected = self._ledger.mark_canceled(
                    hold["id"], reason=f"fill_reconcile: 미체결({start_date}~{end_date})",
                )
            elif apply and verdict == VERDICT_PARTIAL:
                corrected = self._ledger.adjust_qty(
                    hold["id"], filled,
                    reason=f"fill_reconcile: 부분체결 {filled}/{ledger_qty}",
                )

            counts[verdict] += 1
            diffs.append({
                "trade_id": hold["id"],
                "symbol": hold.get("symbol", ""),
                "order_no": order_no,
                "ledger_qty": ledger_qty,
                "filled_qty": filled,
                "verdict": verdict,
                "corrected": corrected,
            })

        if apply and (counts[VERDICT_UNFILLED] or counts[VERDICT_PARTIAL]):
            self._logger.info(
                f"[overseas_fill_reconcile] 원장 보정 — exchange={exchange} "
                f"unfilled={counts[VERDICT_UNFILLED]} partial={counts[VERDICT_PARTIAL]}"
            )

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="해외 체결 대사 완료",
            data={
                "exchange": exchange,
                "start_date": start_date,
                "end_date": end_date,
                "applied": bool(apply),
                "checked": len(holds),
                "counts": counts,
                "diffs": diffs,
            },
        )

    @staticmethod
    def _filled_qty_by_order(data: Any) -> Dict[str, int]:
        """체결내역 응답에서 {주문번호: 체결수량 합}을 만든다.

        한 주문이 여러 체결 행으로 쪼개져 오므로 합산한다.
        """
        if isinstance(data, dict):
            rows = data.get("output")
        else:
            rows = data
        if not isinstance(rows, list):
            return {}

        totals: Dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            order_no = _first_key(row, _ORDER_NO_KEYS)
            if not order_no:
                continue
            totals[order_no] = totals.get(order_no, 0) + _to_int(_first_key(row, _FILLED_QTY_KEYS))
        return totals
