"""국내업종 현재지수(등락 종목수)·시장별 투자자매매동향 조회 단위 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from common.types import ResCommonResponse, ErrorCode


@pytest.fixture
def quotations():
    mock_env = MagicMock()
    mock_env.active_config = {
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://mock-base",
        "custtype": "P",
        "paths": {},
        "params": {},
        "market_code": "J",
    }

    trid_provider = MagicMock()
    trid_provider.quotations.return_value = "FHPUP02100000"

    with patch("brokers.korea_investment.korea_invest_api_base.httpx.AsyncClient"):
        api = KoreaInvestApiQuotations(
            env=mock_env,
            logger=MagicMock(),
            market_clock=MagicMock(),
            header_provider=MagicMock(),
            url_provider=MagicMock(),
            trid_provider=trid_provider,
        )
    return api


def _ok(output):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"output": output})


# ── 국내업종 현재지수 (상승/보합/하락 종목수) ─────────────────────────────

@pytest.mark.asyncio
async def test_index_price_sends_index_market_params(quotations):
    quotations.call_api = AsyncMock(return_value=_ok({"ascn_issu_cnt": "380"}))

    await quotations.inquire_index_price("1001")

    args, kwargs = quotations.call_api.call_args
    assert args[0] == "GET"
    assert args[1] == EndpointKey.INDEX_PRICE
    params = kwargs["params"]
    # 업종/지수는 시장분류 "U" 로 조회해야 한다 (주식 "J" 아님).
    assert params["fid_cond_mrkt_div_code"] == "U"
    assert params["fid_input_iscd"] == "1001"


@pytest.mark.asyncio
async def test_index_price_returns_issue_counts(quotations):
    quotations.call_api = AsyncMock(return_value=_ok({
        "bstp_nmix_prpr": "6579.04",
        "ascn_issu_cnt": "380",
        "uplm_issu_cnt": "3",
        "stnr_issu_cnt": "53",
        "down_issu_cnt": "477",
        "lslm_issu_cnt": "0",
    }))

    result = await quotations.inquire_index_price("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["ascn_issu_cnt"] == "380"
    assert result.data["down_issu_cnt"] == "477"


@pytest.mark.asyncio
async def test_index_price_accepts_list_shaped_output(quotations):
    # 일부 응답은 output 을 단일 원소 리스트로 준다.
    quotations.call_api = AsyncMock(return_value=_ok([{"ascn_issu_cnt": "380"}]))

    result = await quotations.inquire_index_price("0001")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["ascn_issu_cnt"] == "380"


@pytest.mark.asyncio
async def test_index_price_reports_empty_output(quotations):
    quotations.call_api = AsyncMock(return_value=_ok({}))

    result = await quotations.inquire_index_price("0001")

    assert result.rt_cd == ErrorCode.MISSING_KEY.value


@pytest.mark.asyncio
async def test_index_price_propagates_api_error(quotations):
    quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None,
    ))

    result = await quotations.inquire_index_price("0001")

    assert result.rt_cd == ErrorCode.API_ERROR.value


# ── 시장별 투자자매매동향(일별) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_investor_by_market_sends_market_specific_params(quotations):
    quotations.call_api = AsyncMock(return_value=_ok([{"stck_bsop_date": "20260812"}]))

    await quotations.inquire_investor_daily_by_market("1001", date="20260812")

    args, kwargs = quotations.call_api.call_args
    assert args[1] == EndpointKey.INVESTOR_DAILY_BY_MARKET
    params = kwargs["params"]
    assert params["fid_cond_mrkt_div_code"] == "U"
    assert params["fid_input_iscd"] == "1001"
    # 코스닥은 업종 구분이 KSQ (코스피는 KSP).
    assert params["fid_input_iscd_1"] == "KSQ"
    assert params["fid_input_iscd_2"] == "1001"
    # 시작일과 종료일은 같은 날짜여야 한다 (KIS 스펙).
    assert params["fid_input_date_1"] == "20260812"
    assert params["fid_input_date_2"] == "20260812"


@pytest.mark.asyncio
async def test_investor_by_market_maps_kospi_to_ksp(quotations):
    quotations.call_api = AsyncMock(return_value=_ok([{"stck_bsop_date": "20260812"}]))

    await quotations.inquire_investor_daily_by_market("0001", date="20260812")

    assert quotations.call_api.call_args.kwargs["params"]["fid_input_iscd_1"] == "KSP"


@pytest.mark.asyncio
async def test_investor_by_market_rejects_unsupported_index(quotations):
    quotations.call_api = AsyncMock()

    result = await quotations.inquire_investor_daily_by_market("2001", date="20260812")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    quotations.call_api.assert_not_called()


@pytest.mark.asyncio
async def test_investor_by_market_returns_rows(quotations):
    quotations.call_api = AsyncMock(return_value=_ok([
        {"stck_bsop_date": "20260812", "prsn_ntby_tr_pbmn": "-3191200",
         "frgn_ntby_tr_pbmn": "2835700", "orgn_ntby_tr_pbmn": "527700"},
        {"stck_bsop_date": "20260811", "prsn_ntby_tr_pbmn": "120000"},
    ]))

    result = await quotations.inquire_investor_daily_by_market("0001", date="20260812")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 2
    assert result.data[0]["frgn_ntby_tr_pbmn"] == "2835700"


@pytest.mark.asyncio
async def test_investor_by_market_reports_empty_rows(quotations):
    quotations.call_api = AsyncMock(return_value=_ok([]))

    result = await quotations.inquire_investor_daily_by_market("0001", date="20260812")

    assert result.rt_cd == ErrorCode.MISSING_KEY.value


@pytest.mark.asyncio
async def test_investor_by_market_propagates_api_error(quotations):
    quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="모의투자 미지원", data=None,
    ))

    result = await quotations.inquire_investor_daily_by_market("0001", date="20260812")

    assert result.rt_cd == ErrorCode.API_ERROR.value
