from __future__ import annotations

from typing import Any, Optional

from common.types import Exchange, OrderExecutionReport, OrderSide, OrderState, ResCommonResponse


class BrokerOrderResponseMapper:
    """KIS order submit/query/signing payloads를 내부 주문 이벤트로 정규화한다."""

    _ORDER_NO_KEYS = (
        "ordno",
        "order_no",
        "odno",
        "ORDNO",
        "ORDER_NO",
        "ODNO",
        "ODER_NO",
        "주문번호",
    )

    @classmethod
    def extract_broker_order_no(cls, result: Optional[ResCommonResponse]) -> Optional[str]:
        if not result or not result.data:
            return None
        if hasattr(result.data, "ordno"):
            value = getattr(result.data, "ordno")
            return str(value).strip() if value else None
        if isinstance(result.data, dict):
            return cls.extract_broker_order_no_from_dict(result.data)
        return None

    @classmethod
    def extract_broker_order_no_from_dict(cls, data: dict) -> Optional[str]:
        for key in cls._ORDER_NO_KEYS:
            value = data.get(key)
            if value:
                return str(value).strip()
        output = data.get("output")
        if isinstance(output, dict):
            return cls.extract_broker_order_no_from_dict(output)
        return None

    @classmethod
    def from_signing_notice(cls, data: dict, *, tr_id: str = "") -> OrderExecutionReport:
        side = cls._parse_side(data.get("매도매수구분") or data.get("SELN_BYOV_CLS"))
        fill_qty = cls._to_int(data.get("체결수량") or data.get("CNTG_QTY"))
        order_qty = cls._to_int(data.get("주문수량") or data.get("ODER_QTY")) or None
        rejected = str(data.get("거부여부") or data.get("RFUS_YN") or "").upper() == "Y"
        accepted = str(data.get("접수여부") or data.get("ACPT_YN") or "").upper() == "Y"
        concluded = str(data.get("체결여부") or data.get("CNTG_YN") or "") == "2"

        if rejected:
            event_state = OrderState.REJECTED
        elif concluded and order_qty and fill_qty >= order_qty:
            event_state = OrderState.FILLED
        elif concluded and fill_qty > 0:
            event_state = OrderState.PARTIAL_FILLED
        elif accepted:
            event_state = OrderState.SUBMITTED
        else:
            event_state = OrderState.SUBMITTED

        return OrderExecutionReport(
            broker_order_no=str(data.get("주문번호") or data.get("ODER_NO") or "").strip(),
            original_order_no=str(data.get("원주문번호") or data.get("OODER_NO") or "").strip() or None,
            stock_code=str(data.get("주식단축종목코드") or data.get("STCK_SHRN_ISCD") or "").strip(),
            side=side,
            exchange=cls._parse_exchange(data.get("주문거래소구분") or data.get("ORD_EXG_GB")),
            event_state=event_state,
            order_qty=order_qty,
            fill_qty=fill_qty,
            fill_price=cls._to_int(data.get("체결단가") or data.get("CNTG_UNPR")),
            event_time=str(data.get("주식체결시간") or data.get("STCK_CNTG_HOUR") or ""),
            source=f"websocket:{tr_id}" if tr_id else "websocket",
            message="거부" if rejected else ("체결" if concluded else "접수"),
            raw=data,
        )

    @classmethod
    def from_order_query(cls, data: dict, *, tr_id: str = "") -> OrderExecutionReport:
        order_qty = cls._to_int(data.get("ord_qty") or data.get("ORD_QTY") or data.get("주문수량")) or None
        filled_qty = cls._to_int(data.get("tot_ccld_qty") or data.get("TOT_CCLD_QTY") or data.get("체결수량"))
        raw_remaining_qty = data.get("rmn_qty") or data.get("RMN_QTY") or data.get("잔여수량")
        remaining_qty = cls._to_int(raw_remaining_qty) if raw_remaining_qty not in (None, "") else None
        rejected_qty = cls._to_int(data.get("rjct_qty") or data.get("RJCT_QTY") or data.get("거부수량"))
        canceled_qty = cls._to_int(data.get("cncl_cfrm_qty") or data.get("CNCL_CFRM_QTY") or data.get("취소확인수량"))
        canceled = str(data.get("cncl_yn") or data.get("CNCL_YN") or "").upper() == "Y"

        if rejected_qty and filled_qty == 0 and (order_qty is None or rejected_qty >= order_qty):
            event_state = OrderState.REJECTED
        elif canceled or (
            canceled_qty
            and (
                remaining_qty == 0
                or order_qty is None
                or filled_qty + canceled_qty >= order_qty
            )
        ):
            event_state = OrderState.CANCELED
        elif order_qty and filled_qty >= order_qty:
            event_state = OrderState.FILLED
        elif remaining_qty is not None and remaining_qty == 0 and filled_qty > 0:
            event_state = OrderState.FILLED
        elif filled_qty > 0:
            event_state = OrderState.PARTIAL_FILLED
        else:
            event_state = OrderState.SUBMITTED

        event_time = str(
            data.get("ord_dt")
            or data.get("ORD_DT")
            or data.get("주문일자")
            or ""
        ) + str(data.get("ord_tmd") or data.get("ORD_TMD") or data.get("주문시각") or "")

        return OrderExecutionReport(
            broker_order_no=str(data.get("odno") or data.get("ODNO") or data.get("주문번호") or "").strip(),
            original_order_no=str(data.get("orgn_odno") or data.get("ORGN_ODNO") or data.get("원주문번호") or "").strip() or None,
            stock_code=str(data.get("pdno") or data.get("PDNO") or data.get("종목코드") or "").strip(),
            side=cls._parse_side(data.get("sll_buy_dvsn_cd") or data.get("SLL_BUY_DVSN_CD") or data.get("매도매수구분")),
            exchange=cls._parse_exchange(
                data.get("excg_id_dvsn_cd")
                or data.get("EXCG_ID_DVSN_CD")
                or data.get("excg_dvsn_cd")
                or data.get("EXCG_DVSN_CD")
                or data.get("거래소구분")
            ),
            event_state=event_state,
            order_qty=order_qty,
            fill_qty=filled_qty,
            fill_price=cls._to_int(data.get("avg_prvs") or data.get("AVG_PRVS") or data.get("평균가")),
            cumulative_filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            event_time=event_time,
            source=f"polling:{tr_id}" if tr_id else "polling",
            message="주문조회",
            raw=data,
        )

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            text = str(value or "").replace(",", "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_side(value: Any) -> Optional[OrderSide]:
        side_value = str(value or "").strip().upper()
        if side_value in ("01", "1", "매도", "SELL"):
            return OrderSide.SELL
        if side_value in ("02", "2", "매수", "BUY"):
            return OrderSide.BUY
        return None

    @staticmethod
    def _parse_exchange(value: Any) -> Exchange:
        exchange_value = str(value or "").upper()
        if exchange_value in (Exchange.NXT.value, "NX"):
            return Exchange.NXT
        return Exchange.KRX
