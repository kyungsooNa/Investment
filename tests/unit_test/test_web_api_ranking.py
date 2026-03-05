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


# ── 외국인 순매수/순매도 랭킹 ──────────────────────────────

@pytest.mark.asyncio
async def test_get_ranking_foreign_buy(web_client, mock_web_ctx):
    """GET /api/ranking/foreign_buy 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "삼성전자", "glob_ntby_qty": "500"}]
    )
    response = web_client.get("/api/ranking/foreign_buy")
    assert response.status_code == 200
    assert response.json()["data"][0]["hts_kor_isnm"] == "삼성전자"
    mock_web_ctx.stock_query_service.handle_get_top_stocks.assert_awaited_with("foreign_buy")


@pytest.mark.asyncio
async def test_get_ranking_foreign_sell(web_client, mock_web_ctx):
    """GET /api/ranking/foreign_sell 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "SK하이닉스", "glob_ntby_qty": "-200"}]
    )
    response = web_client.get("/api/ranking/foreign_sell")
    assert response.status_code == 200
    assert response.json()["data"][0]["glob_ntby_qty"] == "-200"


@pytest.mark.asyncio
async def test_get_ranking_foreign_empty_data(web_client, mock_web_ctx):
    """외국인 랭킹 데이터 수집 중 (빈 배열 반환)."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="데이터 수집 중...", data=[]
    )
    response = web_client.get("/api/ranking/foreign_buy")
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert "수집 중" in response.json()["msg1"]


# ── 기관/개인 순매수/순매도 랭킹 ──────────────────────────────

@pytest.mark.asyncio
async def test_get_ranking_inst_buy(web_client, mock_web_ctx):
    """GET /api/ranking/inst_buy 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "삼성전자", "orgn_ntby_qty": "300"}]
    )
    response = web_client.get("/api/ranking/inst_buy")
    assert response.status_code == 200
    assert response.json()["data"][0]["orgn_ntby_qty"] == "300"
    mock_web_ctx.stock_query_service.handle_get_top_stocks.assert_awaited_with("inst_buy")


@pytest.mark.asyncio
async def test_get_ranking_inst_sell(web_client, mock_web_ctx):
    """GET /api/ranking/inst_sell 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "NAVER", "orgn_ntby_qty": "-150"}]
    )
    response = web_client.get("/api/ranking/inst_sell")
    assert response.status_code == 200
    assert response.json()["data"][0]["orgn_ntby_qty"] == "-150"


@pytest.mark.asyncio
async def test_get_ranking_prsn_buy(web_client, mock_web_ctx):
    """GET /api/ranking/prsn_buy 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "카카오", "prsn_ntby_qty": "1000"}]
    )
    response = web_client.get("/api/ranking/prsn_buy")
    assert response.status_code == 200
    assert response.json()["data"][0]["prsn_ntby_qty"] == "1000"


@pytest.mark.asyncio
async def test_get_ranking_prsn_sell(web_client, mock_web_ctx):
    """GET /api/ranking/prsn_sell 정상 응답."""
    mock_web_ctx.stock_query_service.handle_get_top_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success",
        data=[{"data_rank": "1", "hts_kor_isnm": "SK하이닉스", "prsn_ntby_qty": "-500"}]
    )
    response = web_client.get("/api/ranking/prsn_sell")
    assert response.status_code == 200
    assert response.json()["data"][0]["prsn_ntby_qty"] == "-500"


# ── 진행률 조회 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ranking_progress(web_client, mock_web_ctx):
    """GET /api/ranking/progress 정상 응답."""
    mock_web_ctx.background_service.get_investor_ranking_progress.return_value = {
        "running": True, "processed": 500, "total": 2500, "collected": 120, "elapsed": 45.3
    }
    response = web_client.get("/api/ranking/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert body["processed"] == 500
    assert body["total"] == 2500
    assert body["collected"] == 120


@pytest.mark.asyncio
async def test_get_ranking_progress_no_background_service(web_client, mock_web_ctx):
    """background_service 없을 때 기본값 반환."""
    mock_web_ctx.background_service = None
    response = web_client.get("/api/ranking/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["total"] == 0
