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


def test_post_ranking_ai_analysis_requires_enabled_ai(web_client, mock_web_ctx):
    mock_web_ctx.ai_analysis_service = None

    response = web_client.post(
        "/api/ranking/ai-analysis",
        json={"candidates": [{"code": "005930", "name": "삼성전자"}]},
    )

    assert response.status_code == 503
    assert "AI 분석" in response.json()["detail"]


def test_post_ranking_ai_analysis_rejects_provider_override(web_client, mock_web_ctx):
    mock_web_ctx.ai_analysis_service = AsyncMock()

    response = web_client.post(
        "/api/ranking/ai-analysis",
        json={
            "candidates": [{"code": "005930", "name": "삼성전자"}],
            "provider_name": "openai",
            "model": "arbitrary-model",
        },
    )

    assert response.status_code == 422
    mock_web_ctx.ai_analysis_service.analyze_leading_stocks.assert_not_awaited()


def test_post_ranking_ai_analysis_returns_429_when_daily_limit_is_reached(
    web_client, mock_web_ctx
):
    from services.ai_usage_limiter import AiUsageLimitExceeded

    mock_web_ctx.ai_analysis_service = AsyncMock()
    mock_web_ctx.ai_analysis_service.analyze_leading_stocks.side_effect = (
        AiUsageLimitExceeded(
            limit_kind="interactive",
            daily_limit=100,
            used=80,
            reset_at="2026-07-20T00:00:00-07:00",
            interactive_limit=80,
            disclosure_reserve=20,
        )
    )

    response = web_client.post(
        "/api/ranking/ai-analysis",
        json={"candidates": [{"code": "005930", "name": "삼성전자"}]},
    )

    assert response.status_code == 429
    assert "일일" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_get_period_ranking_progress(web_client, mock_web_ctx):
    """GET /api/ranking/period_progress 는 기간수급 수집 진행률을 반환한다."""
    mock_web_ctx.ranking_task.get_period_ranking_progress.return_value = {
        "running": True, "processed": 800, "total": 2800, "collected": 310, "elapsed": 190.5
    }
    response = web_client.get("/api/ranking/period_progress")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is True
    assert body["processed"] == 800
    assert body["total"] == 2800
    assert body["collected"] == 310


@pytest.mark.asyncio
async def test_get_period_ranking_progress_no_ranking_task(web_client, mock_web_ctx):
    """ranking_task 없을 때 기본값 반환."""
    mock_web_ctx.ranking_task = None
    response = web_client.get("/api/ranking/period_progress")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_get_domestic_heatmap(web_client, mock_web_ctx):
    """GET /api/heatmap/domestic 은 저장된 시총 스냅샷을 기준일과 함께 반환한다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "-0.72", "market_cap": 12101797,
         "trade_date": "20260730", "market": "KOSPI", "current_price": 70000},
    ])

    response = web_client.get("/api/heatmap/domestic?limit=300")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["trade_date"] == "20260730"
    assert body["data"]["items"] == [{
        "code": "005930",
        "name": "삼성전자",
        "change_rate": "-0.72",
        "market_cap": 12101797,
        "market": "KOSPI",
        "sector": None,
    }]
    # 기본은 일간 등락률이므로 기간 기준가를 조회하지 않는다.
    assert body["data"]["period"] == "1d"
    assert body["data"]["base_date"] is None
    mock_web_ctx.stock_repository.get_market_cap_snapshot.assert_awaited_once_with(limit=300, market=None)
    assert not mock_web_ctx.stock_repository.get_period_base_closes.called


@pytest.mark.asyncio
async def test_get_domestic_heatmap_period_uses_base_close(web_client, mock_web_ctx):
    """period 지정 시 등락률이 해당 기간 기준종가 대비 수익률로 바뀐다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "-0.72", "market_cap": 12101797,
         "trade_date": "20260807", "market": "KOSPI", "current_price": 120000},
        {"code": "000660", "name": "SK하이닉스", "change_rate": "1.10", "market_cap": 9000000,
         "trade_date": "20260807", "market": "KOSPI", "current_price": 90000},
    ])
    mock_web_ctx.stock_repository.get_period_base_closes = AsyncMock(return_value={
        "base_date": "20260508", "latest_date": "20260807", "closes": {"005930": 100000},
    })

    response = web_client.get("/api/heatmap/domestic?limit=300&period=3m")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["period"] == "3m"
    assert body["data"]["base_date"] == "20260508"
    assert body["data"]["items"][0]["change_rate"] == 20.0
    # 기준종가가 없는 종목은 회색(unknown) 타일이 되도록 None 으로 비운다.
    assert body["data"]["items"][1]["change_rate"] is None
    mock_web_ctx.stock_repository.get_period_base_closes.assert_awaited_once_with(period_days=91)


@pytest.mark.asyncio
async def test_get_domestic_heatmap_period_without_history(web_client, mock_web_ctx):
    """기간만큼 이력이 없으면 기준일 없이 안내 메시지를 낸다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "-0.72", "market_cap": 12101797,
         "trade_date": "20260807", "market": "KOSPI", "current_price": 120000},
    ])
    mock_web_ctx.stock_repository.get_period_base_closes = AsyncMock(return_value={
        "base_date": None, "latest_date": "20260807", "closes": {},
    })

    response = web_client.get("/api/heatmap/domestic?period=1y")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["base_date"] is None
    assert body["data"]["items"][0]["change_rate"] is None
    assert "1년" in body["msg1"]


@pytest.mark.asyncio
async def test_get_domestic_heatmap_ytd_uses_first_close_of_year(web_client, mock_web_ctx):
    """period=ytd 는 달력일이 아니라 올해 첫 거래일 종가를 기준으로 저장소에 요청한다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "-0.72", "market_cap": 12101797,
         "trade_date": "20260807", "market": "KOSPI", "current_price": 120000},
    ])
    mock_web_ctx.stock_repository.get_period_base_closes = AsyncMock(return_value={
        "base_date": "20260102", "latest_date": "20260807", "closes": {"005930": 100000},
    })

    response = web_client.get("/api/heatmap/domestic?period=ytd")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["period"] == "ytd"
    assert body["data"]["base_date"] == "20260102"
    assert body["data"]["items"][0]["change_rate"] == 20.0
    mock_web_ctx.stock_repository.get_period_base_closes.assert_awaited_once_with(ytd=True)


@pytest.mark.asyncio
async def test_get_domestic_heatmap_rejects_unknown_period(web_client, mock_web_ctx):
    """지원하지 않는 기간 문자열은 400 으로 거절한다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[])

    response = web_client.get("/api/heatmap/domestic?period=7y")

    assert response.status_code == 400
    mock_web_ctx.stock_repository.get_market_cap_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_domestic_heatmap_with_market_filter(web_client, mock_web_ctx):
    """GET /api/heatmap/domestic?market=KOSDAQ 는 market 필터를 저장소에 전달한다."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[])

    response = web_client.get("/api/heatmap/domestic?limit=100&market=KOSDAQ")

    assert response.status_code == 200
    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"] == {
        "trade_date": None, "items": [], "period": "1d", "base_date": None, "realtime": False,
    }
    mock_web_ctx.stock_repository.get_market_cap_snapshot.assert_awaited_once_with(limit=100, market="KOSDAQ")


@pytest.mark.asyncio
async def test_get_domestic_heatmap_without_repository(web_client, mock_web_ctx):
    """저장소가 없으면 실패 코드로 응답한다."""
    mock_web_ctx.stock_repository = None

    response = web_client.get("/api/heatmap/domestic")

    assert response.status_code == 200
    assert response.json()["rt_cd"] != "0"


@pytest.mark.asyncio
async def test_get_domestic_heatmap_attaches_industry_sector(web_client, mock_web_ctx):
    """업종 분류가 있으면 item 에 sector 를 붙인다(화면이 섹터 블록으로 묶는다)."""
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "1.0", "market_cap": 100,
         "trade_date": "20260814", "market": "KOSPI", "current_price": 1},
        {"code": "999999", "name": "미분류주", "change_rate": "1.0", "market_cap": 50,
         "trade_date": "20260814", "market": "KOSPI", "current_price": 1},
    ])
    mock_web_ctx.stock_classification_repository.get_code_category_map = AsyncMock(
        return_value={"005930": "반도체와반도체장비"}
    )

    body = web_client.get("/api/heatmap/domestic").json()

    items = {item["code"]: item for item in body["data"]["items"]}
    assert items["005930"]["sector"] == "반도체와반도체장비"
    assert items["999999"]["sector"] is None, "분류가 없는 종목은 None (화면이 기타로 묶는다)"
    mock_web_ctx.stock_classification_repository.get_code_category_map.assert_awaited_with("industry")


@pytest.mark.asyncio
async def test_get_domestic_heatmap_without_classification_repository(web_client, mock_web_ctx):
    """분류 저장소가 없으면 sector 없이 기존처럼 단일 그리드로 그린다."""
    mock_web_ctx.stock_classification_repository = None
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[
        {"code": "005930", "name": "삼성전자", "change_rate": "1.0", "market_cap": 100,
         "trade_date": "20260814", "market": "KOSPI", "current_price": 1},
    ])

    body = web_client.get("/api/heatmap/domestic").json()

    assert body["rt_cd"] == "0"
    assert body["data"]["items"][0]["sector"] is None


def _naver_service(rows=None, error=None):
    """장중 스냅샷 서비스 대역. rows 를 주면 그대로, error 를 주면 던진다."""
    service = AsyncMock()
    if error is not None:
        service.get_snapshot.side_effect = error
    else:
        service.get_snapshot.return_value = rows or []
    return service


_LIVE_ROW = {
    "code": "005930", "name": "삼성전자", "change_rate": "2.43", "market_cap": 16048035,
    "market": "KOSPI", "current_price": 274500, "trade_date": "20260814",
}


@pytest.mark.asyncio
async def test_get_domestic_heatmap_uses_live_snapshot_during_market_hours(web_client, mock_web_ctx):
    """장중에는 daily_prices 종가 대신 네이버 실시간 스냅샷을 쓴다."""
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=True)
    mock_web_ctx.naver_market_snapshot_service = _naver_service([_LIVE_ROW])

    response = web_client.get("/api/heatmap/domestic?limit=300")

    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["realtime"] is True
    assert body["data"]["items"][0]["change_rate"] == "2.43"
    assert body["data"]["trade_date"] == "20260814"
    mock_web_ctx.naver_market_snapshot_service.get_snapshot.assert_awaited_once_with(
        limit=300, market=None, allowed_codes=None
    )
    assert not mock_web_ctx.stock_repository.get_market_cap_snapshot.called


@pytest.mark.asyncio
async def test_get_domestic_heatmap_uses_stored_snapshot_outside_market_hours(web_client, mock_web_ctx):
    """장외에는 네이버도 종가만 주므로 긁지 않고 저장소를 쓴다."""
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=False)
    mock_web_ctx.naver_market_snapshot_service = _naver_service([_LIVE_ROW])
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[{
        "code": "005930", "name": "삼성전자", "change_rate": "4.89", "market_cap": 15668027,
        "market": "KOSPI", "current_price": 268000, "trade_date": "20260813",
    }])

    response = web_client.get("/api/heatmap/domestic?limit=300")

    body = response.json()
    assert body["data"]["realtime"] is False
    assert body["data"]["trade_date"] == "20260813"
    assert not mock_web_ctx.naver_market_snapshot_service.get_snapshot.called


@pytest.mark.asyncio
async def test_get_domestic_heatmap_falls_back_when_live_snapshot_is_empty(web_client, mock_web_ctx):
    """마크업이 바뀌어 파싱이 0행이 되어도 화면이 비면 안 된다."""
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=True)
    mock_web_ctx.naver_market_snapshot_service = _naver_service([])
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[{
        "code": "005930", "name": "삼성전자", "change_rate": "4.89", "market_cap": 15668027,
        "market": "KOSPI", "current_price": 268000, "trade_date": "20260813",
    }])

    response = web_client.get("/api/heatmap/domestic?limit=300")

    body = response.json()
    assert body["data"]["realtime"] is False
    assert len(body["data"]["items"]) == 1
    assert mock_web_ctx.stock_repository.get_market_cap_snapshot.called


@pytest.mark.asyncio
async def test_get_domestic_heatmap_falls_back_when_live_snapshot_raises(web_client, mock_web_ctx):
    """스크래핑이 터져도 장마감 스냅샷으로 화면을 살린다."""
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=True)
    mock_web_ctx.naver_market_snapshot_service = _naver_service(error=RuntimeError("네이버 응답 없음"))
    mock_web_ctx.stock_repository.get_market_cap_snapshot = AsyncMock(return_value=[{
        "code": "005930", "name": "삼성전자", "change_rate": "4.89", "market_cap": 15668027,
        "market": "KOSPI", "current_price": 268000, "trade_date": "20260813",
    }])

    response = web_client.get("/api/heatmap/domestic?limit=300")

    body = response.json()
    assert body["rt_cd"] == "0"
    assert body["data"]["realtime"] is False
    assert len(body["data"]["items"]) == 1


@pytest.mark.asyncio
async def test_get_domestic_heatmap_live_snapshot_reuses_the_stored_universe(web_client, mock_web_ctx):
    """장중 소스(네이버)는 ETF·우선주까지 싣는다. 장외와 같은 종목 집합을 그려야 하므로
    저장소가 가진 유니버스로 잘라 넘긴다."""
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=True)
    mock_web_ctx.naver_market_snapshot_service = _naver_service([_LIVE_ROW])
    mock_web_ctx.stock_repository.get_snapshot_codes = AsyncMock(return_value={"005930", "000660"})

    web_client.get("/api/heatmap/domestic?limit=300")

    mock_web_ctx.naver_market_snapshot_service.get_snapshot.assert_awaited_once_with(
        limit=300, market=None, allowed_codes={"005930", "000660"}
    )


@pytest.mark.asyncio
async def test_get_domestic_heatmap_live_snapshot_honours_market_filter(web_client, mock_web_ctx):
    mock_web_ctx.is_market_open_now = AsyncMock(return_value=True)
    mock_web_ctx.naver_market_snapshot_service = _naver_service([_LIVE_ROW])

    mock_web_ctx.stock_repository.get_snapshot_codes = AsyncMock(return_value={"005930"})

    web_client.get("/api/heatmap/domestic?limit=100&market=KOSDAQ")

    mock_web_ctx.naver_market_snapshot_service.get_snapshot.assert_awaited_once_with(
        limit=100, market="KOSDAQ", allowed_codes={"005930"}
    )
