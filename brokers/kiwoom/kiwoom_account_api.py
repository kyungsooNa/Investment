"""키움 계좌 API (Phase 5).

kt00018(계좌평가잔고내역) + kt00001(예수금상세현황)을 합쳐 KIS `get_account_balance`
계약과 같은 `{"output1": [...], "output2": [{...}]}` 형태로 돌려준다. 소비처
(`order_execution_service`·`fill_reconciliation_service`·`balance.js`)가 KIS 필드명을
직접 읽으므로 브로커를 바꿔도 그대로 동작해야 한다.

실측 함정 2건 (공식 response_example 기준):
- 금액·수량이 0 으로 좌측패딩돼 온다(`000000025789890`). 평문 정수로 정규화한다.
- 종목코드에 `A` 접두가 붙는다(`A005930`). 떼지 않으면 `pdno` 로 종목을 찾지 못한다.

계좌번호는 body 로 넘기지 않는다 — 키움은 토큰(appkey)에 계좌가 묶여 있어
KIS 처럼 `CANO`/`ACNT_PRDT_CD` 를 실어 보낼 자리가 없다.
"""
import re

from brokers.kiwoom.kiwoom_api_base import KiwoomApiBase
from brokers.kiwoom.kiwoom_url_provider import KiwoomApiId, path_for
from common.types import ErrorCode, Exchange, ResCommonResponse

_HOLDINGS_KEY = "acnt_evlt_remn_indv_tot"

# 키움 국내거래소구분 (kt00018 `dmst_stex_tp`). 통합(UN)은 대응 개념이 없어 KRX 로 본다.
_EXCHANGE_CODE = {
    Exchange.KRX: "KRX",
    Exchange.NXT: "NXT",
    Exchange.UN: "KRX",
}

# 키움 보유종목 -> KIS output1 필드명.
_HOLDING_FIELDS = {
    "stk_nm": "prdt_name",
    "rmnd_qty": "hldg_qty",
    "trde_able_qty": "ord_psbl_qty",
    "cur_prc": "prpr",
    "pur_pric": "pchs_avg_pric",
    "pur_amt": "pchs_amt",
    "evlt_amt": "evlu_amt",
    "evltv_prft": "evlu_pfls_amt",
    "pred_close_pric": "prdy_prpr",
}

# 키움 계좌합계 -> KIS output2 필드명.
_SUMMARY_FIELDS = {
    "tot_evlt_amt": "tot_evlu_amt",
    "tot_evlt_pl": "evlu_pfls_smtl_amt",
    "tot_pur_amt": "pchs_amt_smtl_amt",
}

_PADDED = re.compile(r"^([+-]?)0*(\d+)$")


def _unpad(value) -> str:
    """`000000025789890` / `-00000000196888` 을 평문 정수 문자열로 바꾼다.

    소수(`-52.71`)나 숫자가 아닌 값은 그대로 둔다.
    """
    text = str(value if value is not None else "").strip()
    match = _PADDED.match(text)
    if not match:
        return text
    sign, digits = match.groups()
    return f"{sign}{digits.lstrip('0') or '0'}"


def _strip_code_prefix(code) -> str:
    """키움 종목코드의 `A` 접두를 뗀다 (`A005930` -> `005930`)."""
    text = str(code if code is not None else "").strip()
    if len(text) > 1 and text[0].isalpha() and text[1:].isdigit():
        return text[1:]
    return text


class KiwoomAccountApi(KiwoomApiBase):
    """키움 계좌 조회 API."""

    async def _call(self, api_id: str, body: dict) -> ResCommonResponse:
        return await self.call_api("POST", path_for(api_id), api_id=api_id, data=body)

    def _to_holdings(self, rows) -> list:
        holdings = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _strip_code_prefix(row.get("stk_cd"))
            if not code:
                self._logger.warning(f"종목코드 없는 잔고 행 스킵: {row}")
                continue

            mapped = {"pdno": code}
            for src, dst in _HOLDING_FIELDS.items():
                if row.get(src) not in (None, ""):
                    mapped[dst] = _unpad(row[src])
            # 수익률은 패딩이 없는 소수라 원본을 유지한다.
            if row.get("prft_rt") not in (None, ""):
                mapped["evlu_pfls_rt"] = str(row["prft_rt"]).strip()
            holdings.append(mapped)
        return holdings

    async def get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """보유 종목 + 계좌 합계 + 예수금을 KIS 계약 형태로 반환한다."""
        balance = await self._call(KiwoomApiId.ACCOUNT_BALANCE, {
            "qry_tp": "1",  # 1:합산
            "dmst_stex_tp": _EXCHANGE_CODE.get(exchange, "KRX"),
        })
        if balance.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"키움 잔고 조회 실패: {balance.msg1}")
            return balance

        payload = balance.data if isinstance(balance.data, dict) else {}
        summary = {
            dst: _unpad(payload[src])
            for src, dst in _SUMMARY_FIELDS.items()
            if payload.get(src) not in (None, "")
        }

        # 예수금은 별도 API 다. 실패해도 보유 종목 조회는 살린다 — 전량매도 같은
        # output1 소비 경로가 예수금 장애로 막히지 않게. 다만 모르는 예수금을
        # 0 으로 채우지는 않는다(키 자체를 넣지 않는다).
        deposit = await self._call(KiwoomApiId.ACCOUNT_DEPOSIT, {"qry_tp": "2"})  # 2:일반조회
        if deposit.rt_cd == ErrorCode.SUCCESS.value:
            cash = deposit.data if isinstance(deposit.data, dict) else {}
            if cash.get("entr") not in (None, ""):
                summary["dnca_tot_amt"] = _unpad(cash["entr"])
            if cash.get("ord_alow_amt") not in (None, ""):
                summary["ord_psbl_cash"] = _unpad(cash["ord_alow_amt"])
        else:
            self._logger.warning(f"키움 예수금 조회 실패(잔고는 유지): {deposit.msg1}")

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={
                "output1": self._to_holdings(payload.get(_HOLDINGS_KEY) or []),
                "output2": [summary],
            },
        )
