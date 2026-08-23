"""키움 계좌 API (Phase 5) 단위 테스트.

스펙 출처: 키움 공식 저장소 `Kiwoom-Securities/kiwoom-rest-api` 의
`kiwoom/_data/kiwoom_api_spec.json` — kt00018(계좌평가잔고내역) · kt00001(예수금상세현황).
fixture 는 공식 response_example 의 표기를 그대로 쓴다(0 좌측패딩 · 종목코드 `A` 접두).
"""
from unittest.mock import AsyncMock, MagicMock

from brokers.kiwoom.kiwoom_account_api import KiwoomAccountApi
from common.types import ErrorCode, Exchange, ResCommonResponse


def _make_api(responses):
    """api_id -> ResCommonResponse 매핑으로 call_api 를 대체한다."""
    env = MagicMock()
    env.get_base_url.return_value = "https://mockapi.kiwoom.com"
    env.get_access_token = AsyncMock(return_value="tok")

    api = KiwoomAccountApi(env, logger=MagicMock())

    async def fake_call(method, path, *, api_id, params=None, data=None):
        return responses[api_id]

    api.call_api = AsyncMock(side_effect=fake_call)
    return api


def _ok(payload):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=payload)


# 공식 스펙 response_example 축약본 — 0 좌측패딩과 A 접두를 보존한다.
_KT00018 = {
    "tot_pur_amt": "000000017598258",
    "tot_evlt_amt": "000000025789890",
    "tot_evlt_pl": "000000008138825",
    "tot_prft_rt": "46.25",
    "acnt_evlt_remn_indv_tot": [
        {
            "stk_cd": "A005930",
            "stk_nm": "삼성전자",
            "evltv_prft": "-00000000196888",
            "prft_rt": "-52.71",
            "pur_pric": "000000000124500",
            "rmnd_qty": "000000000000003",
            "trde_able_qty": "000000000000003",
            "cur_prc": "000000059000",
            "pur_amt": "000000000373500",
            "evlt_amt": "000000000177000",
        },
    ],
    "return_code": 0,
}

_KT00001 = {
    "entr": "000000000017534",
    "ord_alow_amt": "000000000012000",
    "return_code": 0,
}

_BOTH = {"kt00018": _ok(_KT00018), "kt00001": _ok(_KT00001)}


class TestHoldingsMapping:
    async def test_maps_holdings_to_kis_output1_contract(self):
        api = _make_api(_BOTH)

        result = await api.get_account_balance()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        holdings = result.data["output1"]
        assert len(holdings) == 1
        row = holdings[0]
        # 키움 종목코드는 `A` 접두가 붙는다 — 떼지 않으면 pdno 로 종목을 못 찾는다.
        assert row["pdno"] == "005930"
        assert row["prdt_name"] == "삼성전자"
        # 값은 0 으로 좌측패딩돼 온다 — 평문 정수로 정규화한다.
        assert row["hldg_qty"] == "3"
        assert row["prpr"] == "59000"
        assert row["pchs_avg_pric"] == "124500"
        assert row["evlu_amt"] == "177000"
        assert row["ord_psbl_qty"] == "3"
        # 부호가 붙은 값도 정규화되되 부호는 남는다.
        assert row["evlu_pfls_amt"] == "-196888"
        # 수익률은 패딩이 없는 소수라 원본 유지.
        assert row["evlu_pfls_rt"] == "-52.71"

    async def test_summary_maps_to_kis_output2_contract(self):
        api = _make_api(_BOTH)

        result = await api.get_account_balance()

        summary = result.data["output2"][0]
        assert summary["tot_evlu_amt"] == "25789890"
        assert summary["evlu_pfls_smtl_amt"] == "8138825"
        assert summary["pchs_amt_smtl_amt"] == "17598258"
        # 예수금은 kt00001 에서 온다.
        assert summary["dnca_tot_amt"] == "17534"
        assert summary["ord_psbl_cash"] == "12000"

    async def test_sends_exchange_and_query_type(self):
        api = _make_api(_BOTH)

        await api.get_account_balance(exchange=Exchange.NXT)

        calls = {c.kwargs["api_id"]: c.kwargs["data"] for c in api.call_api.call_args_list}
        assert calls["kt00018"]["dmst_stex_tp"] == "NXT"
        assert calls["kt00018"]["qry_tp"] == "1"
        assert calls["kt00001"]["qry_tp"] == "2"

    async def test_krx_is_default_exchange(self):
        api = _make_api(_BOTH)

        await api.get_account_balance()

        calls = {c.kwargs["api_id"]: c.kwargs["data"] for c in api.call_api.call_args_list}
        assert calls["kt00018"]["dmst_stex_tp"] == "KRX"

    async def test_empty_holdings_is_success_with_empty_list(self):
        api = _make_api({
            "kt00018": _ok({"tot_evlt_amt": "000000000000000", "acnt_evlt_remn_indv_tot": [], "return_code": 0}),
            "kt00001": _ok(_KT00001),
        })

        result = await api.get_account_balance()

        # 보유 종목이 없는 것은 오류가 아니다.
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert result.data["output1"] == []

    async def test_skips_rows_without_stock_code(self):
        api = _make_api({
            "kt00018": _ok({"acnt_evlt_remn_indv_tot": [{"stk_nm": "이름만"}], "return_code": 0}),
            "kt00001": _ok(_KT00001),
        })

        result = await api.get_account_balance()

        assert result.data["output1"] == []


class TestFailureHandling:
    async def test_balance_failure_propagates(self):
        api = _make_api({
            "kt00018": ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="권한없음", data=None),
            "kt00001": _ok(_KT00001),
        })

        result = await api.get_account_balance()

        assert result.rt_cd == ErrorCode.API_ERROR.value

    async def test_deposit_failure_keeps_holdings_but_omits_cash(self):
        """예수금 조회가 실패해도 보유 종목 조회는 살린다(전량매도 경로가 막히지 않게).

        단 모르는 예수금을 0 으로 채워 넣지는 않는다 — 키 자체를 넣지 않는다.
        """
        api = _make_api({
            "kt00018": _ok(_KT00018),
            "kt00001": ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="일시오류", data=None),
        })

        result = await api.get_account_balance()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data["output1"]) == 1
        summary = result.data["output2"][0]
        assert "dnca_tot_amt" not in summary
        assert summary["tot_evlu_amt"] == "25789890"
