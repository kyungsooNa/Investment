"""
랭킹/시가총액 관련 테스트 (ranking.html, marketcap.html).
"""
import pytest
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_get_ranking(web_client, mock_web_ctx):
    """GET /api/ranking/{category} 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"name": "Stock A"}]
    )

    # 정상 케이스
    response = web_client.get("/api/ranking/rise")
    assert response.status_code == 200
    assert response.json()["data"][0]["name"] == "Stock A"
    mock_web_ctx.stock_query_service.handle_get_top_stocks.assert_awaited_once_with("rise")

    # 잘못된 카테고리
    response = web_client.get("/api/ranking/invalid_cat")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_ranking_failure(web_client, mock_web_ctx):
    """GET /api/ranking 실패 응답 테스트"""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=None
    )
    response = web_client.get("/api/ranking/rise")
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"


@pytest.mark.asyncio
async def test_get_top_market_cap(web_client, mock_web_ctx):
    """GET /api/top-market-cap 엔드포인트 테스트"""
    mock_web_ctx.broker.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{
            "hts_kor_isnm": "Samsung",
            "mksc_shrn_iscd": "005930",
            "stck_prpr": "70000",
            "prdy_ctrt": "0.0",
            "stck_avls": "1000000"
        }]
    )

    response = web_client.get("/api/top-market-cap")
    assert response.status_code == 200
    assert response.json()["data"][0]["name"] == "Samsung"
    mock_web_ctx.broker.get_top_market_cap_stocks_code.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_top_market_cap_fallback(web_client, mock_web_ctx):
    """GET /api/top-market-cap 잘못된 market 코드 테스트"""
    mock_web_ctx.broker.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[]
    )
    response = web_client.get("/api/top-market-cap?market=9999")
    assert response.status_code == 200
    mock_web_ctx.broker.get_top_market_cap_stocks_code.assert_awaited_with("0001", 20)


@pytest.mark.asyncio
async def test_get_top_market_cap_failure(web_client, mock_web_ctx):
    """GET /api/top-market-cap 실패 응답 테스트"""
    mock_web_ctx.broker.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    )

    response = web_client.get("/api/top-market-cap")
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"
