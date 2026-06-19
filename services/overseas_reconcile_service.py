# services/overseas_reconcile_service.py
"""해외 포지션 reconcile 서비스 (Phase 5).

로컬 기대 포지션(paper/canary 추적)과 브로커 해외 잔고(`get_overseas_balance`)를
비교해 drift(missing/extra/qty_mismatch)를 감지한다. **순수 비교 — 주문 경로 없음.**

국내 `FillReconciliationService`(OrderStateMachine 결합, 주문 FSM 보정)와 달리, 해외는
FSM/포지션 스토어가 아직 없고 live 가 잠겨 있으므로 자기완결적 drift 리포트만 산출한다.
실 캐너리 가동(live_enabled) 단계에서 로컬 추적과 조인해 사용한다.

브로커 잔고 응답은 공식 표본이 부족해 표본별 키가 갈리므로 다중 후보 키로 관용
파싱한다(실 fixture 확보 시 단일화).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common.types import ErrorCode

# KIS 해외 잔고(TTTS3012R) output1 보유종목의 심볼/수량 후보 키.
_SYMBOL_KEYS = ("ovrs_pdno", "pdno", "OVRS_PDNO", "PDNO")
_QTY_KEYS = ("ovrs_cblc_qty", "cblc_qty", "hldg_qty", "ord_psbl_qty",
             "OVRS_CBLC_QTY", "CBLC_QTY")


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class OverseasReconcileService:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    @staticmethod
    def parse_broker_positions(balance_response) -> Dict[str, int]:
        """해외 잔고 응답에서 {심볼(대문자): 보유수량}을 관용 추출한다.

        rt_cd 실패 / data 비정상 / 0·blank 수량은 모두 제외한다.
        """
        if balance_response is None:
            return {}
        if getattr(balance_response, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return {}
        data = getattr(balance_response, "data", None)
        if not isinstance(data, dict):
            return {}
        holdings = data.get("output1")
        if not isinstance(holdings, list):
            return {}

        positions: Dict[str, int] = {}
        for row in holdings:
            if not isinstance(row, dict):
                continue
            symbol = ""
            for k in _SYMBOL_KEYS:
                if row.get(k):
                    symbol = str(row.get(k)).strip().upper()
                    break
            if not symbol:
                continue
            qty = 0
            for k in _QTY_KEYS:
                if k in row:
                    qty = _to_int(row.get(k))
                    break
            if qty > 0:
                positions[symbol] = positions.get(symbol, 0) + qty
        return positions

    def reconcile(
        self,
        local_positions: Dict[str, int],
        balance_response,
    ) -> Dict[str, Any]:
        """로컬 기대 포지션과 브로커 잔고를 비교해 drift 리포트를 반환한다.

        반환: {ok, matched, missing_in_broker, extra_in_broker, qty_mismatch,
               broker_positions, [error]}
        잔고 조회 실패 시 ok=False + error="balance_query_failed" — 로컬을 함부로
        missing 으로 단정하지 않는다(조회 불가 ≠ 미보유).
        """
        local = {str(s).upper(): _to_int(q) for s, q in (local_positions or {}).items()}

        if getattr(balance_response, "rt_cd", None) != ErrorCode.SUCCESS.value:
            self._logger.warning({
                "event": "overseas_reconcile_balance_failed",
                "msg": getattr(balance_response, "msg1", ""),
            })
            return {
                "ok": False,
                "error": "balance_query_failed",
                "matched": [],
                "missing_in_broker": [],
                "extra_in_broker": [],
                "qty_mismatch": [],
                "broker_positions": {},
            }

        broker = self.parse_broker_positions(balance_response)

        matched: List[str] = []
        missing: List[Dict[str, Any]] = []
        extra: List[Dict[str, Any]] = []
        mismatch: List[Dict[str, Any]] = []

        for symbol, lqty in local.items():
            bqty = broker.get(symbol, 0)
            if bqty == 0:
                missing.append({"symbol": symbol, "local_qty": lqty})
            elif bqty != lqty:
                mismatch.append({"symbol": symbol, "local_qty": lqty, "broker_qty": bqty})
            else:
                matched.append(symbol)

        for symbol, bqty in broker.items():
            if symbol not in local:
                extra.append({"symbol": symbol, "broker_qty": bqty})

        ok = not (missing or extra or mismatch)
        report = {
            "ok": ok,
            "matched": sorted(matched),
            "missing_in_broker": sorted(missing, key=lambda d: d["symbol"]),
            "extra_in_broker": sorted(extra, key=lambda d: d["symbol"]),
            "qty_mismatch": sorted(mismatch, key=lambda d: d["symbol"]),
            "broker_positions": broker,
        }
        if not ok:
            self._logger.warning({"event": "overseas_reconcile_drift",
                                  "missing": len(missing), "extra": len(extra),
                                  "qty_mismatch": len(mismatch)})
        return report
