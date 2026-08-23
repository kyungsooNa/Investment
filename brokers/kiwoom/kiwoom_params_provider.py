"""키움 REST 시세 API 의 request body 를 조립한다.

종목코드는 거래소별 접미사를 붙인다 — KRX `039490` / NXT `039490_NX` / SOR `039490_AL`
(공식 스펙 `stk_cd` 필드 설명). 통합(UN)은 키움에 대응 개념이 없어 KRX 로 취급한다.
"""
from common.types import Exchange

_EXCHANGE_SUFFIX = {
    Exchange.KRX: "",
    Exchange.NXT: "_NX",
    Exchange.UN: "",
}


def stock_code(code: str, exchange: Exchange = Exchange.KRX) -> str:
    return f"{code}{_EXCHANGE_SUFFIX.get(exchange, '')}"


def stock_info(code: str, exchange: Exchange = Exchange.KRX) -> dict:
    return {"stk_cd": stock_code(code, exchange)}


def daily_chart(code: str, base_date: str, exchange: Exchange = Exchange.KRX,
                adjusted: bool = True) -> dict:
    return {
        "stk_cd": stock_code(code, exchange),
        "base_dt": base_date,
        "upd_stkpc_tp": "1" if adjusted else "0",
    }


def minute_chart(code: str, tick_scope: str, base_date: str = "",
                 exchange: Exchange = Exchange.KRX, adjusted: bool = True) -> dict:
    body = {
        "stk_cd": stock_code(code, exchange),
        "tic_scope": str(tick_scope),
        "upd_stkpc_tp": "1" if adjusted else "0",
    }
    if base_date:
        body["base_dt"] = base_date
    return body
