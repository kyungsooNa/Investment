"""국내업종(코스피/코스닥) 기간별 지수 시세 조회 단위 테스트."""
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
        "paths": {"inquire_daily_indexchartprice": "/mock/indexchartprice"},
        "params": {},
        "market_code": "J",
    }

    trid_provider = MagicMock()
    trid_provider.quotations.return_value = "FHKUP03500100"

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


def _api_response(output1=None, output2=None):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "output1": output1 if output1 is not None else {},
            "output2": output2 if output2 is not None else [],
        },
    )


@pytest.mark.asyncio
async def test_index_chart_returns_summary_and_candles(quotations):
    quotations.call_api = AsyncMock(return_value=_api_response(
        output1={
            "hts_kor_isnm": "코스피",
            "bstp_nmix_prpr": "2650.15",
            "bstp_nmix_prdy_vrss": "12.30",
            "prdy_vrss_sign": "2",
            "bstp_nmix_prdy_ctrt": "0.47",
        },
        output2=[
            {"stck_bsop_date": "20260727", "bstp_nmix_prpr": "2650.15"},
            {"stck_bsop_date": "20260724", "bstp_nmix_prpr": "2637.85"},
        ],
    ))

    result = await quotations.inquire_daily_indexchartprice("0001", "20260601", "20260727")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["summary"]["hts_kor_isnm"] == "코스피"
    assert len(result.data["candles"]) == 2
    assert result.data["candles"][0]["stck_bsop_date"] == "20260727"


@pytest.mark.asyncio
async def test_index_chart_sends_index_market_params(quotations):
    quotations.call_api = AsyncMock(return_value=_api_response(output2=[{"stck_bsop_date": "20260727"}]))

    await quotations.inquire_daily_indexchartprice("1001", "20260601", "20260727")

    args, kwargs = quotations.call_api.call_args
    assert args[0] == "GET"
    assert args[1] == EndpointKey.DAILY_INDEXCHARTPRICE
    params = kwargs["params"]
    # 업종/지수는 시장분류 "U" 로 조회해야 한다 (주식 "J" 아님).
    assert params["fid_cond_mrkt_div_code"] == "U"
    assert params["fid_input_iscd"] == "1001"
    assert params["fid_input_date_1"] == "20260601"
    assert params["fid_input_date_2"] == "20260727"
    assert params["fid_period_div_code"] == "D"


@pytest.mark.asyncio
async def test_index_chart_rejects_unsupported_period(quotations):
    quotations.call_api = AsyncMock()

    result = await quotations.inquire_daily_indexchartprice("0001", "20260601", "20260727", period="X")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    quotations.call_api.assert_not_called()


@pytest.mark.asyncio
async def test_index_chart_propagates_api_error(quotations):
    quotations.call_api = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None,
    ))

    result = await quotations.inquire_daily_indexchartprice("0001", "20260601", "20260727")

    assert result.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_index_chart_reports_empty_candles(quotations):
    quotations.call_api = AsyncMock(return_value=_api_response(output1={"bstp_nmix_prpr": "2650"}, output2=[]))

    result = await quotations.inquire_daily_indexchartprice("0001", "20260601", "20260727")

    assert result.rt_cd == ErrorCode.MISSING_KEY.value
