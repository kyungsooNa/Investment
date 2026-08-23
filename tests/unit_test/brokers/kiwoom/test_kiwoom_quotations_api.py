"""키움 시세 API (Phase 4) 단위 테스트.

스펙 출처: 키움 공식 저장소 `Kiwoom-Securities/kiwoom-rest-api` 의
`kiwoom/_data/kiwoom_api_spec.json` — ka10001(주식기본정보) · ka10081(일봉) · ka10080(분봉).
응답 예시의 필드명·부호 표기(`+600`)를 그대로 fixture 로 쓴다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.kiwoom.kiwoom_quotations_api import (
    _PLAIN_FIELDS,
    _PRICE_FIELDS,
    KiwoomQuotationsApi,
)
from common.types import (
    ErrorCode,
    Exchange,
    ResCommonResponse,
    ResStockFullInfoApiOutput,
)


def _make_api(response):
    env = MagicMock()
    env.get_base_url.return_value = "https://mockapi.kiwoom.com"
    env.get_access_token = AsyncMock(return_value="tok")

    api = KiwoomQuotationsApi(env, logger=MagicMock())
    api.call_api = AsyncMock(return_value=response)
    return api


def _ok(payload):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=payload)


# 공식 스펙 response_example 축약본
_KA10001 = {
    "stk_cd": "005930",
    "stk_nm": "삼성전자",
    "cur_prc": "+70100",
    "open_pric": "69800",
    "high_pric": "70500",
    "low_pric": "-69600",
    "pred_pre": "+600",
    "pre_sig": "2",
    "flu_rt": "+0.86",
    "trde_qty": "9263135",
    "mac": "4184000",
    "per": "13.10",
    "eps": "5350",
    "pbr": "1.20",
    "250hgst": "88800",
    "250lwst": "49900",
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}

_KA10081 = {
    "stk_cd": "005930",
    "stk_dt_pole_chart_qry": [
        {"cur_prc": "70100", "trde_qty": "9263135", "trde_prica": "648525", "dt": "20250908",
         "open_pric": "69800", "high_pric": "70500", "low_pric": "69600",
         "pred_pre": "+600", "pred_pre_sig": "2", "trde_tern_rt": "+0.16"},
        {"cur_prc": "-69500", "trde_qty": "11526724", "trde_prica": "804642", "dt": "20250905",
         "open_pric": "70300", "high_pric": "70400", "low_pric": "69500",
         "pred_pre": "-600", "pred_pre_sig": "5", "trde_tern_rt": "+0.19"},
    ],
    "return_code": 0,
}

_KA10080 = {
    "stk_cd": "005930",
    "stk_min_pole_chart_qry": [
        {"cur_prc": "+70100", "trde_qty": "1234", "cntr_tm": "20250908153000",
         "open_pric": "69800", "high_pric": "70500", "low_pric": "69600",
         "pred_pre": "+600", "pred_pre_sig": "2"},
    ],
    "return_code": 0,
}


class TestGetCurrentPrice:
    async def test_maps_kiwoom_fields_to_common_output(self):
        api = _make_api(_ok(_KA10001))

        result = await api.get_current_price("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        output = result.data["output"]
        # 키움 가격은 부호 접두(+/-)가 붙는다 — 시세는 절대값으로 정규화해야 한다.
        assert output.stck_prpr == "70100"
        assert output.stck_lwpr == "69600"
        assert output.hts_avls == "4184000"
        # 전일대비는 부호 자체가 의미라 보존한다.
        assert output.prdy_vrss == "+600"
        assert output.prdy_ctrt == "+0.86"
        assert output.acml_vol == "9263135"

    async def test_uses_ka10001_with_plain_code_for_krx(self):
        api = _make_api(_ok(_KA10001))

        await api.get_current_price("005930", exchange=Exchange.KRX)

        _, kwargs = api.call_api.call_args
        assert kwargs["api_id"] == "ka10001"
        assert kwargs["data"]["stk_cd"] == "005930"

    async def test_appends_nxt_suffix_to_stock_code(self):
        api = _make_api(_ok(_KA10001))

        await api.get_current_price("039490", exchange=Exchange.NXT)

        _, kwargs = api.call_api.call_args
        assert kwargs["data"]["stk_cd"] == "039490_NX"

    async def test_propagates_business_error(self):
        api = _make_api(ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="한도초과", data=None))

        result = await api.get_current_price("005930")

        assert result.rt_cd == ErrorCode.API_ERROR.value

    async def test_empty_payload_does_not_raise(self):
        api = _make_api(_ok({}))

        result = await api.get_current_price("005930")

        # 빈 응답을 0원 시세로 흘려보내면 안 된다.
        assert result.rt_cd != ErrorCode.SUCCESS.value


class TestGetStockInfoByCode:
    async def test_returns_full_info_model(self):
        api = _make_api(_ok(_KA10001))

        result = await api.get_stock_info_by_code("005930")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert result.data.stck_oprc == "69800"
        assert result.data.stck_prpr == "70100"
        assert result.data.per == "13.10"
        assert result.data.d250_hgpr == "88800"


class TestDailyChart:
    async def test_maps_daily_rows(self):
        api = _make_api(_ok(_KA10081))

        result = await api.inquire_daily_itemchartprice("005930", "20250901", "20250908")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 2
        first = result.data[0]
        assert first.stck_bsop_date == "20250908"
        assert first.stck_clpr == "70100"
        assert first.stck_oprc == "69800"
        assert first.acml_vol == "9263135"
        # 부호 접두가 붙은 종가도 정규화된다.
        assert result.data[1].stck_clpr == "69500"

    async def test_sends_required_body_fields(self):
        api = _make_api(_ok(_KA10081))

        await api.inquire_daily_itemchartprice("005930", "20250901", "20250908")

        _, kwargs = api.call_api.call_args
        assert kwargs["api_id"] == "ka10081"
        body = kwargs["data"]
        assert body["stk_cd"] == "005930"
        assert body["base_dt"] == "20250908"   # 기준일자는 조회 종료일
        assert body["upd_stkpc_tp"] == "1"     # 수정주가 적용

    async def test_empty_rows_return_missing_key(self):
        api = _make_api(_ok({"stk_cd": "005930", "stk_dt_pole_chart_qry": [], "return_code": 0}))

        result = await api.inquire_daily_itemchartprice("005930", "20250901", "20250908")

        assert result.rt_cd == ErrorCode.MISSING_KEY.value
        assert result.data == []

    async def test_rejects_unsupported_period(self):
        api = _make_api(_ok(_KA10081))

        result = await api.inquire_daily_itemchartprice("005930", "", "", fid_period_div_code="X")

        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        api.call_api.assert_not_awaited()

    async def test_skips_rows_missing_required_fields(self):
        broken = {"stk_cd": "005930", "return_code": 0, "stk_dt_pole_chart_qry": [
            {"dt": "20250908", "open_pric": "1", "high_pric": "2", "low_pric": "3", "cur_prc": "4"},
            {"open_pric": "1"},  # 일자 없음 → 스킵
        ]}
        api = _make_api(_ok(broken))

        result = await api.inquire_daily_itemchartprice("005930", "", "20250908")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 1


class TestMinuteChart:
    async def test_maps_minute_rows_using_cntr_tm(self):
        api = _make_api(_ok(_KA10080))

        result = await api.get_intraday_minutes("005930", tick_scope="1")

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 1
        assert result.data[0].stck_bsop_date == "20250908153000"
        assert result.data[0].stck_clpr == "70100"

    async def test_sends_tick_scope(self):
        api = _make_api(_ok(_KA10080))

        await api.get_intraday_minutes("005930", tick_scope="5")

        _, kwargs = api.call_api.call_args
        assert kwargs["api_id"] == "ka10080"
        assert kwargs["data"]["tic_scope"] == "5"

    async def test_rejects_unsupported_tick_scope(self):
        api = _make_api(_ok(_KA10080))

        result = await api.get_intraday_minutes("005930", tick_scope="7")

        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        api.call_api.assert_not_awaited()


def test_every_mapping_target_exists_on_the_model():
    """대응 필드 없는 매핑은 pydantic 이 조용히 버려 값이 소리 없이 사라진다.

    (실제로 `hts_kor_isnm`·`roe` 를 매핑했다가 이 방식으로 유실되는 것을 확인했다.)
    """
    targets = {**_PRICE_FIELDS, **_PLAIN_FIELDS}
    unknown = sorted(
        f"{src} -> {dst}"
        for src, dst in targets.items()
        if dst not in ResStockFullInfoApiOutput.model_fields
    )

    assert not unknown, (
        "ResStockFullInfoApiOutput 에 없는 매핑 대상이 있습니다(값이 조용히 유실됨):\n"
        + "\n".join(unknown)
    )
