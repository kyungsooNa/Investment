"""
랭킹/시가총액 관련 테스트 (ranking.html, marketcap.html).
"""
import pytest
from unittest.mock import AsyncMock
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_post_ranking_ai_analysis_success(web_client, mock_web_ctx):
    """POST /api/ranking/ai-analysis 는 후보 목록을 AI 분석 서비스로 전달한다."""
    mock_web_ctx.ai_analysis_service = AsyncMock()
    mock_web_ctx.ai_analysis_service.analyze_leading_stocks.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="AI 분석 성공",
        data={"analysis": "강한 후보입니다.", "provider": "gemini", "model": "gemini-test"},
    )

    response = web_client.post(
        "/api/ranking/ai-analysis",
        json={
            "candidates": [{"code": "005930", "name": "삼성전자", "prdy_ctrt": "3.1"}],
            "market_context": {"category": "rise"},
            "max_candidates": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["analysis"] == "강한 후보입니다."
    mock_web_ctx.ai_analysis_service.analyze_leading_stocks.assert_awaited_once_with(
        [{"code": "005930", "name": "삼성전자", "prdy_ctrt": "3.1"}],
        market_context={"category": "rise"},
        max_candidates=10,
    )


@pytest.mark.asyncio
async def test_post_ranking_ai_analysis_empty_candidates(web_client, mock_web_ctx):
    """후보가 없으면 AI provider 생성/호출 없이 EMPTY_VALUES 응답을 반환한다."""
    mock_web_ctx.ai_analysis_service = AsyncMock()

    response = web_client.post("/api/ranking/ai-analysis", json={"candidates": []})

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] != "0"
    assert "후보" in body["msg1"]
    mock_web_ctx.ai_analysis_service.analyze_leading_stocks.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_theme_leaders_success(web_client, mock_web_ctx):
    """GET /api/ranking/theme_leaders 정상 응답 + 기본 category_types=theme."""
    mock_web_ctx.theme_leader_service = AsyncMock()
    mock_web_ctx.theme_leader_service.get_theme_leaders.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공",
        data=[{"normalized_name": "로봇", "sources": ["NAVER"], "group_rs_median": 88.0,
               "member_count": 3, "leaders": [{"code": "005930", "name": "삼성전자",
                                               "rs_rating": 99, "sources": ["NAVER"]}]}],
    )
    response = web_client.get("/api/ranking/theme_leaders")
    assert response.status_code == 200
    assert response.json()["data"][0]["normalized_name"] == "로봇"
    mock_web_ctx.theme_leader_service.get_theme_leaders.assert_awaited_once_with(
        category_types=("theme",)
    )


@pytest.mark.asyncio
async def test_get_theme_leaders_include_industry(web_client, mock_web_ctx):
    """include_industry=true 면 category_types 에 industry 가 포함된다."""
    mock_web_ctx.theme_leader_service = AsyncMock()
    mock_web_ctx.theme_leader_service.get_theme_leaders.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data=[]
    )
    response = web_client.get("/api/ranking/theme_leaders?include_industry=true")
    assert response.status_code == 200
    mock_web_ctx.theme_leader_service.get_theme_leaders.assert_awaited_once_with(
        category_types=("theme", "industry")
    )


@pytest.mark.asyncio
async def test_get_theme_leaders_service_missing(web_client, mock_web_ctx):
    """ThemeLeaderService 미설정 시 rt_cd=1 안내."""
    mock_web_ctx.theme_leader_service = None
    response = web_client.get("/api/ranking/theme_leaders")
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"


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
async def test_get_intraday_theme_leaders_returns_latest_task_report(web_client, mock_web_ctx):
    mock_web_ctx.theme_intraday_leader_alert_task.get_latest_report.return_value = {
        "captured_at": "20260715 10:06",
        "data": [{"normalized_name": "반도체", "recent_trading_value_won": 50_000_000_000}],
    }

    response = web_client.get("/api/ranking/themes/intraday")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"][0]["normalized_name"] == "반도체"


@pytest.mark.asyncio
async def test_get_intraday_theme_leaders_handles_unavailable_task(web_client, mock_web_ctx):
    mock_web_ctx.theme_intraday_leader_alert_task = None

    response = web_client.get("/api/ranking/themes/intraday")

    assert response.status_code == 200
    assert response.json()["rt_cd"] != "0"


@pytest.mark.asyncio
async def test_get_ytd_ranking(web_client, mock_web_ctx):
    """GET /api/ranking/ytd 는 저장된 연초 대비 수익률 랭킹을 반환한다."""
    mock_web_ctx.stock_repository.get_ytd_return_ranking = AsyncMock(return_value=[{
        "code": "005930",
        "name": "삼성전자",
        "current_price": 75000,
        "base_price": 50000,
        "base_date": "20260102",
        "latest_date": "20260713",
        "ytd_return_rate": 50.0,
        "data_rank": "1",
    }])

    response = web_client.get("/api/ranking/ytd?limit=30")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"][0]["ytd_return_rate"] == 50.0
    mock_web_ctx.stock_repository.get_ytd_return_ranking.assert_awaited_once_with(limit=30, market=None)


@pytest.mark.asyncio
async def test_get_ytd_ranking_with_market_filter(web_client, mock_web_ctx):
    """GET /api/ranking/ytd?market=KOSPI 는 market 필터를 그대로 저장소에 전달한다."""
    mock_web_ctx.stock_repository.get_ytd_return_ranking = AsyncMock(return_value=[])

    response = web_client.get("/api/ranking/ytd?limit=30&market=KOSPI")

    assert response.status_code == 200
    mock_web_ctx.stock_repository.get_ytd_return_ranking.assert_awaited_once_with(limit=30, market="KOSPI")


@pytest.mark.asyncio
async def test_get_period_investor_program_ranking(web_client, mock_web_ctx):
    """GET /api/ranking/investor-period 는 기간 수급 랭킹을 반환한다."""
    mock_web_ctx.ranking_task.get_period_investor_program_net_buy_ranking = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="Success",
        data=[{
            "data_rank": "1",
            "industry": "반도체",
            "hts_kor_isnm": "삼성전자",
            "combined_period_ntby_tr_pbmn_won": "300000000",
        }],
    ))

    response = web_client.get("/api/ranking/investor-period?days=5&metric=amount&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"][0]["industry"] == "반도체"
    mock_web_ctx.ranking_task.get_period_investor_program_net_buy_ranking.assert_awaited_once_with(
        days=5,
        metric="amount",
        limit=10,
    )


@pytest.mark.asyncio
async def test_get_period_investor_program_ranking_validates_query(web_client, mock_web_ctx):
    """기간 수급 랭킹은 허용된 days/metric 만 받는다."""
    mock_web_ctx.ranking_task.get_period_investor_program_net_buy_ranking = AsyncMock()

    response = web_client.get("/api/ranking/investor-period?days=7&metric=amount")
    assert response.status_code == 400

    response = web_client.get("/api/ranking/investor-period?days=5&metric=bad")
    assert response.status_code == 400

    mock_web_ctx.ranking_task.get_period_investor_program_net_buy_ranking.assert_not_awaited()


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


# ── Minervini Stage 2 목록 ───────────────────────────────

@pytest.mark.asyncio
async def test_get_minervini_stage2_success(web_client, mock_web_ctx):
    """get_minervini_stage2 - DB에서 정상 데이터 반환."""
    from unittest.mock import AsyncMock
    from common.types import ResCommonResponse
    mock_web_ctx.minervini_stage_service.get_stage2_list = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd="0", msg1="성공",
            data=[{"code": "005930", "name": "삼성전자", "stck_prpr": "70000",
                   "prdy_ctrt": "1.0", "stage": 2, "rs_rating": 85, "market_cap": 1000000}]
        )
    )
    response = web_client.get("/api/ranking/minervini_stage2")
    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"][0]["code"] == "005930"
    mock_web_ctx.minervini_stage_service.get_stage2_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_minervini_stage2_collecting(web_client, mock_web_ctx):
    """get_minervini_stage2 - 수집 중 상태 반환."""
    from unittest.mock import AsyncMock
    from common.types import ResCommonResponse
    mock_web_ctx.minervini_stage_service.get_stage2_list = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="수집 중", data=[])
    )
    response = web_client.get("/api/ranking/minervini_stage2")
    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"] == []
    assert "수집 중" in body["msg1"]


@pytest.mark.asyncio
async def test_get_minervini_stage2_no_service(web_client, mock_web_ctx):
    """get_minervini_stage2 - MinerviniStageService 미설정 시 rt_cd=1."""
    mock_web_ctx.minervini_stage_service = None
    response = web_client.get("/api/ranking/minervini_stage2")
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"


@pytest.mark.asyncio
async def test_get_minervini_stage2_task_not_set(web_client, mock_web_ctx):
    """get_minervini_stage2 - MinerviniUpdateTask 미설정 시 서비스가 rt_cd=1 반환."""
    from unittest.mock import AsyncMock
    from common.types import ResCommonResponse
    mock_web_ctx.minervini_stage_service.get_stage2_list = AsyncMock(
        return_value=ResCommonResponse(rt_cd="1", msg1="MinerviniUpdateTask 미설정", data=None)
    )
    response = web_client.get("/api/ranking/minervini_stage2")
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"
    assert response.json()["data"] is None


# ── 진행률 조회 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ranking_progress(web_client, mock_web_ctx):
    """GET /api/ranking/progress 정상 응답."""
    mock_web_ctx.ranking_task.get_investor_ranking_progress.return_value = {
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
async def test_get_ranking_progress_no_ranking_task(web_client, mock_web_ctx):
    """ranking_task 없을 때 기본값 반환."""
    mock_web_ctx.ranking_task = None
    response = web_client.get("/api/ranking/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["total"] == 0
