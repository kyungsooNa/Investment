"""키움 주문 API (Phase 6).

kt10000(매수) · kt10001(매도) · kt10003(취소)를 KIS `place_stock_order` /
`cancel_stock_order` 계약에 맞춰 감싼다. 응답은 소비처가 읽는 KIS 키
(`data["output"]["ODNO"]`)로 변환한다.

주문 판정 규칙은 KIS 경로와 일부러 동일하게 맞췄다 — 브로커를 바꿨다고 같은
요청이 다른 주문이 되면 안 된다:
- `order_price > 0` 이면 지정가, 아니면 시장가
- 지정가는 `adjust_price` 로 호가단위 보정
- NXT 거래소 시장가 주문은 거절
"""
from brokers.kiwoom.kiwoom_api_base import KiwoomApiBase
from brokers.kiwoom.kiwoom_url_provider import KiwoomApiId, path_for
from common.types import ErrorCode, Exchange, ResCommonResponse
from utils.korea_invest_price_utils import adjust_price

# 키움 매매구분 (kt10000/kt10001 `trde_tp`).
_TRADE_TYPE_LIMIT = "0"    # 보통(지정가)
_TRADE_TYPE_MARKET = "3"   # 시장가

_EXCHANGE_CODE = {
    Exchange.KRX: "KRX",
    Exchange.NXT: "NXT",
    Exchange.UN: "KRX",
}


def _invalid(msg: str) -> ResCommonResponse:
    return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)


class KiwoomTradingApi(KiwoomApiBase):
    """키움 주문 API."""

    async def _call(self, api_id: str, body: dict) -> ResCommonResponse:
        return await self.call_api("POST", path_for(api_id), api_id=api_id, data=body)

    def _order_number_response(self, response: ResCommonResponse, what: str) -> ResCommonResponse:
        """키움 응답의 `ord_no` 를 KIS 계약(`output.ODNO`)으로 옮긴다."""
        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"키움 {what} 실패: {response.msg1}")
            return response

        payload = response.data if isinstance(response.data, dict) else {}
        order_no = str(payload.get("ord_no") or "").strip()
        if not order_no:
            # 주문번호 없는 성공을 통과시키면 이후 체결 대사가 조용히 빈다.
            msg = f"키움 {what} 응답에 주문번호가 없습니다: {payload}"
            self._logger.error(msg)
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=msg, data=None)

        output = {"ODNO": order_no}
        if payload.get("base_orig_ord_no"):
            output["ORGN_ODNO"] = str(payload["base_orig_ord_no"]).strip()
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1=response.msg1,
            data={"output": output},
        )

    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool,
                                exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        try:
            qty = int(order_qty)
            price = int(order_price)
        except (TypeError, ValueError):
            return _invalid(f"주문 수량/가격이 정수가 아닙니다: qty={order_qty}, price={order_price}")

        if qty <= 0:
            return _invalid(f"주문 수량은 1주 이상이어야 합니다: {order_qty}")

        is_limit = price > 0
        if not is_limit and exchange == Exchange.NXT:
            return _invalid("NXT 거래소에서는 시장가 주문을 지원하지 않습니다. 지정가 주문을 사용하세요.")

        body = {
            "dmst_stex_tp": _EXCHANGE_CODE.get(exchange, "KRX"),
            "stk_cd": str(stock_code),
            "ord_qty": str(qty),
            "trde_tp": _TRADE_TYPE_LIMIT if is_limit else _TRADE_TYPE_MARKET,
        }
        if is_limit:
            adjusted = adjust_price(price)
            if adjusted != price:
                self._logger.info(f"호가단위 보정: {price} → {adjusted}")
            body["ord_uv"] = str(adjusted)

        action = "매수" if is_buy else "매도"
        self._logger.info(
            f"키움 주식 {action} 주문 시도 - 종목:{stock_code}, 수량:{qty}, 가격:{price}"
        )
        api_id = KiwoomApiId.ORDER_BUY if is_buy else KiwoomApiId.ORDER_SELL
        return self._order_number_response(await self._call(api_id, body), f"{action} 주문")

    async def cancel_stock_order(self, *, broker_order_no: str, stock_code: str,
                                 order_qty: int = 0,
                                 exchange: Exchange = Exchange.KRX,
                                 **_ignored) -> ResCommonResponse:
        """주문을 취소한다 (kt10003). `order_qty=0` 은 스펙상 잔량 전부 취소다."""
        if not broker_order_no:
            return _invalid("취소할 원주문번호가 없습니다.")
        try:
            qty = int(order_qty)
        except (TypeError, ValueError):
            return _invalid(f"취소 수량이 정수가 아닙니다: {order_qty}")
        if qty < 0:
            return _invalid(f"취소 수량은 0 이상이어야 합니다: {order_qty}")

        body = {
            "dmst_stex_tp": _EXCHANGE_CODE.get(exchange, "KRX"),
            "orig_ord_no": str(broker_order_no),
            "stk_cd": str(stock_code),
            "cncl_qty": str(qty),
        }
        self._logger.info(f"키움 주문 취소 시도 - 원주문:{broker_order_no}, 수량:{qty}")
        return self._order_number_response(
            await self._call(KiwoomApiId.ORDER_CANCEL, body), "주문 취소"
        )
