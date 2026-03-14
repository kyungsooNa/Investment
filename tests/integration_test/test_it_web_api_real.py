# tests/integration_test/test_it_web_api_real.py
"""
실전투자 모드 Web API 통합 테스트.
FastAPI TestClient를 사용하여 HTTP 엔드포인트 → 서비스 레이어 흐름을 검증한다.
모의투자 테스트와 공통 로직은 동일하나, 실전 전용 기능과 환경 차이를 중점 검증.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from common.types import ResCommonResponse, ErrorCode
from tests.integration_test.conftest import make_success_response, make_error_response


# ============================================================================
# 1. 시장 상태 조회 (GET /api/status) — 실전 모드
# ============================================================================

class TestStatusReal:
    def test_get_status_returns_real_env(self, real_client, mock_real_ctx):
        """실전투자 모드에서 env_type이 '실전투자'로 반환된다."""
        resp = real_client.get("/api/status")
        assert resp.status_code == 200

        data = resp.json()
        assert data["env_type"] == "실전투자"
        assert data["initialized"] is True
        assert data["market_open"] is True

    def test_get_status_market_closed(self, real_client, mock_real_ctx):
        """실전 모드에서도 장 마감 시 market_open=False."""
        mock_real_ctx.is_market_operating_hours.return_value = False
        resp = real_client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["market_open"] is False


# ============================================================================
# 2. 현재가 조회 (GET /api/stock/{code}) — 실전 모드
# ============================================================================

class TestStockPriceReal:
    def test_get_stock_price_success(self, real_client, mock_real_ctx):
        """실전 모드 종목 현재가 조회 성공."""
        price_data = {
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_ctrt": "1.73",
            "stck_cntg_hour": "101500",
        }
        mock_real_ctx.stock_query_service.handle_get_current_stock_price = AsyncMock(
            return_value=make_success_response(price_data)
        )

        resp = real_client.get("/api/stock/005930")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["stck_prpr"] == "70500"
        assert body["data"]["prdy_vrss"] == "1200"
        assert body["data"]["prdy_ctrt"] == "1.73"
        mock_real_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")

    def test_get_stock_price_error(self, real_client, mock_real_ctx):
        """실전 모드 API 오류 시 에러 응답."""
        mock_real_ctx.stock_query_service.handle_get_current_stock_price = AsyncMock(
            return_value=make_error_response("시세 조회 불가")
        )

        resp = real_client.get("/api/stock/999999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"


# ============================================================================
# 3. 차트 데이터 (GET /api/chart/{code}) — 실전 모드
# ============================================================================

class TestChartReal:
    @pytest.mark.parametrize("period", ["D", "W", "M", "Y"])
    def test_get_chart_all_periods(self, real_client, mock_real_ctx, period):
        """일/주/월/년봉 차트 조회 성공."""
        ohlcv_data = [
            {"stck_bsop_date": "20260307", "stck_oprc": "70000", "stck_hgpr": "71000",
             "stck_lwpr": "69500", "stck_clpr": "70500", "acml_vol": "15000000"}
        ]
        mock_real_ctx.stock_query_service.get_ohlcv = AsyncMock(
            return_value=make_success_response(ohlcv_data)
        )

        resp = real_client.get(f"/api/chart/005930?period={period}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        mock_real_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with("005930", period)

    def test_get_chart_with_indicators(self, real_client, mock_real_ctx):
        """지표 포함 차트 조회."""
        mock_real_ctx.stock_query_service.get_ohlcv_with_indicators = AsyncMock(
            return_value=make_success_response({"ohlcv": [], "ma": [], "bb": []})
        )

        resp = real_client.get("/api/chart/005930?period=D&indicators=true")
        assert resp.status_code == 200
        mock_real_ctx.stock_query_service.get_ohlcv_with_indicators.assert_awaited_once_with("005930", "D")


# ============================================================================
# 4. 계좌 잔고 조회 (GET /api/balance) — 실전 모드
# ============================================================================

class TestBalanceReal:
    def test_get_balance_real_account(self, real_client, mock_real_ctx):
        """실전투자 잔고 조회 시 계좌 타입이 '실전투자'로 표시된다."""
        balance_data = {
            "dnca_tot_amt": "5000000",
            "tot_evlu_amt": "5500000",
        }
        mock_real_ctx.stock_query_service.handle_get_account_balance = AsyncMock(
            return_value=make_success_response(balance_data)
        )

        resp = real_client.get("/api/balance")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["dnca_tot_amt"] == "5000000"
        assert body["data"]["tot_evlu_amt"] == "5500000"
        assert body["account_info"]["type"] == "실전투자"
        assert body["account_info"]["number"] == "12345678-01"

    def test_get_balance_failure(self, real_client, mock_real_ctx):
        """잔고 조회 실패 시에도 계좌 정보가 포함된다."""
        mock_real_ctx.stock_query_service.handle_get_account_balance = AsyncMock(
            return_value=make_error_response("잔고 조회 실패")
        )

        resp = real_client.get("/api/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"
        assert body["account_info"]["type"] == "실전투자"


# ============================================================================
# 5. 매수 주문 (POST /api/order - buy) — 실전 모드
# ============================================================================

class TestBuyOrderReal:
    def test_buy_order_success(self, real_client, mock_real_ctx):
        """실전 매수 주문 성공."""
        order_result = {"ord_no": "R1234567890"}
        mock_real_ctx.order_execution_service.handle_buy_stock = AsyncMock(
            return_value=make_success_response(order_result)
        )

        resp = real_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        mock_real_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with(
            "005930", "10", "70000"
        )
        mock_real_ctx.virtual_manager.log_buy.assert_called_once_with("수동매매", "005930", 70000)

    def test_buy_order_failure_no_log(self, real_client, mock_real_ctx):
        """매수 실패 시 가상 매매 기록 미생성."""
        mock_real_ctx.order_execution_service.handle_buy_stock = AsyncMock(
            return_value=make_error_response("잔고 부족")
        )

        resp = real_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "1"
        mock_real_ctx.virtual_manager.log_buy.assert_not_called()


# ============================================================================
# 6. 매도 주문 (POST /api/order - sell) — 실전 모드
# ============================================================================

class TestSellOrderReal:
    def test_sell_order_success(self, real_client, mock_real_ctx):
        """실전 매도 주문 성공."""
        order_result = {"ord_no": "S987654321"}
        mock_real_ctx.order_execution_service.handle_sell_stock = AsyncMock(
            return_value=make_success_response(order_result)
        )

        resp = real_client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        mock_real_ctx.order_execution_service.handle_sell_stock.assert_awaited_once_with(
            "005930", "5", "71000"
        )
        mock_real_ctx.virtual_manager.log_sell.assert_called_once_with("005930", 71000)

    def test_sell_order_failure_no_log(self, real_client, mock_real_ctx):
        """매도 실패 시 가상 매매 기록 미생성."""
        mock_real_ctx.order_execution_service.handle_sell_stock = AsyncMock(
            return_value=make_error_response("보유 수량 부족")
        )

        resp = real_client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "1"
        mock_real_ctx.virtual_manager.log_sell.assert_not_called()

    def test_order_invalid_side_returns_400(self, real_client, mock_real_ctx):
        """잘못된 side 값은 400 에러."""
        resp = real_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "hold"
        })
        assert resp.status_code == 400


# ============================================================================
# 7. 랭킹 조회 (GET /api/ranking/{category}) — 실전 모드
# ============================================================================

class TestRankingReal:
    @pytest.mark.parametrize("category", ["rise", "fall", "volume", "trading_value",
                                           "foreign_buy", "foreign_sell",
                                           "inst_buy", "inst_sell",
                                           "prsn_buy", "prsn_sell",
                                           "program_buy", "program_sell"])
    def test_get_ranking_all_categories(self, real_client, mock_real_ctx, category):
        """실전 모드에서 모든 유효 category 정상 처리."""
        mock_real_ctx.stock_query_service.handle_get_top_stocks = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[])
        )

        resp = real_client.get(f"/api/ranking/{category}")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == ErrorCode.SUCCESS.value

    def test_get_ranking_with_data(self, real_client, mock_real_ctx):
        """랭킹 데이터가 올바르게 직렬화된다."""
        ranking_items = [
            MagicMock(to_dict=lambda: {"rank": "1", "name": "삼성전자", "code": "005930", "current_price": "70500"}),
            MagicMock(to_dict=lambda: {"rank": "2", "name": "SK하이닉스", "code": "000660", "current_price": "180000"}),
        ]
        mock_real_ctx.stock_query_service.handle_get_top_stocks = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=ranking_items)
        )

        resp = real_client.get("/api/ranking/rise")
        assert resp.status_code == 200

        body = resp.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "삼성전자"
        assert body["data"][1]["code"] == "000660"

    def test_get_ranking_invalid_category(self, real_client, mock_real_ctx):
        """잘못된 category는 400."""
        resp = real_client.get("/api/ranking/unknown")
        assert resp.status_code == 400


# ============================================================================
# 8. 시가총액 조회 (GET /api/top-market-cap) — 실전 모드
# ============================================================================

class TestMarketCapReal:
    def test_get_top_market_cap_kospi(self, real_client, mock_real_ctx):
        """코스피 시가총액 상위 종목 조회."""
        items = [
            MagicMock(
                hts_kor_isnm="삼성전자", mksc_shrn_iscd="005930",
                stck_prpr="70500", prdy_ctrt="1.73", stck_avls="4200000",
            ),
            MagicMock(
                hts_kor_isnm="SK하이닉스", mksc_shrn_iscd="000660",
                stck_prpr="180000", prdy_ctrt="-0.55", stck_avls="3500000",
            ),
        ]
        for item in items:
            type(item).__instancecheck__ = lambda self, instance: False

        mock_real_ctx.broker.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=items)
        )

        resp = real_client.get("/api/top-market-cap?limit=10&market=0001")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == ErrorCode.SUCCESS.value
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "삼성전자"
        assert body["data"][0]["rank"] == "1"
        assert body["data"][1]["name"] == "SK하이닉스"
        assert body["data"][1]["rank"] == "2"

        mock_real_ctx.broker.get_top_market_cap_stocks_code.assert_awaited_once_with("0001", 10)

    def test_get_top_market_cap_kosdaq(self, real_client, mock_real_ctx):
        """코스닥 시가총액 조회."""
        mock_real_ctx.broker.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[])
        )

        resp = real_client.get("/api/top-market-cap?market=1001")
        assert resp.status_code == 200
        mock_real_ctx.broker.get_top_market_cap_stocks_code.assert_awaited_once_with("1001", 20)

    def test_get_top_market_cap_api_error(self, real_client, mock_real_ctx):
        """시가총액 조회 API 실패 시 에러 응답."""
        mock_real_ctx.broker.get_top_market_cap_stocks_code = AsyncMock(
            return_value=make_error_response("실전 전용 API 호출 실패")
        )

        resp = real_client.get("/api/top-market-cap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"


# ============================================================================
# 9. 기술 지표 (GET /api/indicator/*) — 실전 모드
# ============================================================================

class TestIndicatorsReal:
    def test_get_bollinger_bands(self, real_client, mock_real_ctx):
        """실전 모드 볼린저 밴드 조회."""
        mock_real_ctx.indicator_service.get_bollinger_bands = AsyncMock(
            return_value=make_success_response([
                {"date": "20260307", "upper": 72000, "middle": 70000, "lower": 68000}
            ])
        )

        resp = real_client.get("/api/indicator/bollinger/005930?period=20&std_dev=2.0")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"

    def test_get_rsi(self, real_client, mock_real_ctx):
        """실전 모드 RSI 조회."""
        mock_real_ctx.indicator_service.get_rsi = AsyncMock(
            return_value=make_success_response([{"date": "20260307", "rsi": 72.3}])
        )

        resp = real_client.get("/api/indicator/rsi/005930?period=14")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"

    def test_get_moving_average(self, real_client, mock_real_ctx):
        """실전 모드 이동평균선 조회."""
        mock_real_ctx.indicator_service.get_moving_average = AsyncMock(
            return_value=make_success_response([{"date": "20260307", "ma": 70000}])
        )

        resp = real_client.get("/api/indicator/ma/005930?period=20&method=ema")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"
        mock_real_ctx.indicator_service.get_moving_average.assert_awaited_once_with("005930", 20, "ema")


# ============================================================================
# 10. 환경 전환 (POST /api/environment) — 실전 → 모의
# ============================================================================

class TestEnvironmentReal:
    def test_change_to_paper_environment(self, real_client, mock_real_ctx):
        """실전 → 모의 환경 전환 성공."""
        mock_real_ctx.get_env_type.return_value = "모의투자"

        resp = real_client.post("/api/environment", json={"is_paper": True})
        assert resp.status_code == 200

        body = resp.json()
        assert body["success"] is True
        assert body["env_type"] == "모의투자"
        mock_real_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=True)

    def test_environment_change_failure(self, real_client, mock_real_ctx):
        """환경 전환 실패 시 500 에러."""
        mock_real_ctx.initialize_services = AsyncMock(return_value=False)

        resp = real_client.post("/api/environment", json={"is_paper": True})
        assert resp.status_code == 500


# ============================================================================
# 11. 가상 매매 (GET /api/virtual/*) — 실전 모드
# ============================================================================

class TestVirtualReal:
    def test_get_virtual_summary(self, real_client, mock_real_ctx):
        """가상 매매 요약 정보 조회."""
        mock_real_ctx.virtual_manager.get_summary.return_value = {
            "total_trades": 10,
            "win_rate": 60.0,
            "avg_return": 5.2,
        }

        resp = real_client.get("/api/virtual/summary")
        assert resp.status_code == 200

        body = resp.json()
        assert body["total_trades"] == 10
        assert body["win_rate"] == 60.0

    def test_get_virtual_strategies(self, real_client, mock_real_ctx):
        """등록된 전략 목록 조회."""
        mock_real_ctx.virtual_manager.get_all_strategies.return_value = [
            "수동매매", "모멘텀", "갭상승눌림목"
        ]

        resp = real_client.get("/api/virtual/strategies")
        assert resp.status_code == 200

        body = resp.json()
        assert "수동매매" in body
        assert len(body) == 3


# ============================================================================
# 12. 랭킹 진행률 (GET /api/ranking/progress) — 실전 모드
# ============================================================================

class TestRankingProgressReal:
    def test_get_ranking_progress(self, real_client, mock_real_ctx):
        """투자자 랭킹 수집 진행률 조회."""
        mock_real_ctx.background_service.get_investor_ranking_progress.return_value = {
            "running": True, "processed": 5, "total": 12, "collected": 50, "elapsed": 3.2
        }

        resp = real_client.get("/api/ranking/progress")
        assert resp.status_code == 200

        body = resp.json()
        assert body["running"] is True
        assert body["processed"] == 5
        assert body["total"] == 12

    def test_get_ranking_progress_no_background_service(self, real_client, mock_real_ctx):
        """background_service가 None이면 기본값 반환."""
        mock_real_ctx.background_service = None

        resp = real_client.get("/api/ranking/progress")
        assert resp.status_code == 200

        body = resp.json()
        assert body["running"] is False
        assert body["processed"] == 0
