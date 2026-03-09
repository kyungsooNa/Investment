# tests/integration_test/test_it_web_api_paper.py
"""
모의투자 모드 Web API 통합 테스트.
FastAPI TestClient를 사용하여 HTTP 엔드포인트 → 서비스 레이어 흐름을 검증한다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from common.types import ResCommonResponse, ErrorCode
from tests.integration_test.conftest import make_success_response, make_error_response


# ============================================================================
# 1. 시장 상태 조회 (GET /api/status)
# ============================================================================

class TestStatusPaper:
    def test_get_status_returns_market_info(self, paper_client, mock_paper_ctx):
        """모의투자 모드 시장 상태 조회 시 올바른 정보를 반환한다."""
        resp = paper_client.get("/api/status")
        assert resp.status_code == 200

        data = resp.json()
        assert data["market_open"] is True
        assert data["env_type"] == "모의투자"
        assert data["initialized"] is True
        assert data["current_time"] == "2026-03-08 10:30:00"

    def test_get_status_market_closed(self, paper_client, mock_paper_ctx):
        """장 마감 시 market_open=False를 반환한다."""
        mock_paper_ctx.is_market_open.return_value = False
        resp = paper_client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["market_open"] is False


# ============================================================================
# 2. 현재가 조회 (GET /api/stock/{code})
# ============================================================================

class TestStockPricePaper:
    def test_get_stock_price_success(self, paper_client, mock_paper_ctx):
        """종목 현재가 조회 성공 시 올바른 데이터를 반환한다."""
        price_data = {
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_ctrt": "1.73",
        }
        mock_paper_ctx.stock_query_service.handle_get_current_stock_price = AsyncMock(
            return_value=make_success_response(price_data)
        )

        resp = paper_client.get("/api/stock/005930")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["stck_prpr"] == "70500"

        # 서비스가 올바른 종목코드로 호출되었는지 검증
        mock_paper_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")

    def test_get_stock_price_api_error(self, paper_client, mock_paper_ctx):
        """API 오류 시 에러 응답을 반환한다."""
        mock_paper_ctx.stock_query_service.handle_get_current_stock_price = AsyncMock(
            return_value=make_error_response("종목 조회 실패")
        )

        resp = paper_client.get("/api/stock/999999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"
        assert "실패" in body["msg1"]


# ============================================================================
# 3. 차트 데이터 조회 (GET /api/chart/{code})
# ============================================================================

class TestChartPaper:
    def test_get_chart_daily(self, paper_client, mock_paper_ctx):
        """일봉 차트 조회 성공."""
        ohlcv_data = [
            {"stck_bsop_date": "20260307", "stck_oprc": "70000", "stck_hgpr": "71000",
             "stck_lwpr": "69500", "stck_clpr": "70500", "acml_vol": "15000000"}
        ]
        mock_paper_ctx.stock_query_service.get_ohlcv = AsyncMock(
            return_value=make_success_response(ohlcv_data)
        )

        resp = paper_client.get("/api/chart/005930?period=D")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        mock_paper_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with("005930", "D")

    def test_get_chart_weekly(self, paper_client, mock_paper_ctx):
        """주봉 차트 조회."""
        mock_paper_ctx.stock_query_service.get_ohlcv = AsyncMock(
            return_value=make_success_response([])
        )

        resp = paper_client.get("/api/chart/005930?period=W")
        assert resp.status_code == 200
        mock_paper_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with("005930", "W")

    def test_get_chart_monthly(self, paper_client, mock_paper_ctx):
        """월봉 차트 조회."""
        mock_paper_ctx.stock_query_service.get_ohlcv = AsyncMock(
            return_value=make_success_response([])
        )

        resp = paper_client.get("/api/chart/005930?period=M")
        assert resp.status_code == 200
        mock_paper_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with("005930", "M")

    def test_get_chart_yearly(self, paper_client, mock_paper_ctx):
        """년봉 차트 조회."""
        mock_paper_ctx.stock_query_service.get_ohlcv = AsyncMock(
            return_value=make_success_response([])
        )

        resp = paper_client.get("/api/chart/005930?period=Y")
        assert resp.status_code == 200
        mock_paper_ctx.stock_query_service.get_ohlcv.assert_awaited_once_with("005930", "Y")

    def test_get_chart_with_indicators(self, paper_client, mock_paper_ctx):
        """지표 포함 차트 조회 시 get_ohlcv_with_indicators 호출."""
        mock_paper_ctx.stock_query_service.get_ohlcv_with_indicators = AsyncMock(
            return_value=make_success_response({"ohlcv": [], "ma": [], "bb": []})
        )

        resp = paper_client.get("/api/chart/005930?period=D&indicators=true")
        assert resp.status_code == 200
        mock_paper_ctx.stock_query_service.get_ohlcv_with_indicators.assert_awaited_once_with("005930", "D")


# ============================================================================
# 4. 계좌 잔고 조회 (GET /api/balance)
# ============================================================================

class TestBalancePaper:
    def test_get_balance_success(self, paper_client, mock_paper_ctx):
        """모의투자 잔고 조회 성공 시 계좌 정보가 포함된다."""
        balance_data = {
            "dnca_tot_amt": "1000000",
            "tot_evlu_amt": "1200000",
        }
        mock_paper_ctx.stock_query_service.handle_get_account_balance = AsyncMock(
            return_value=make_success_response(balance_data)
        )

        resp = paper_client.get("/api/balance")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["dnca_tot_amt"] == "1000000"
        assert body["data"]["tot_evlu_amt"] == "1200000"

        # 계좌 정보 확인 (모의투자)
        assert body["account_info"]["type"] == "모의투자"
        assert body["account_info"]["number"] == "12345678-01"

    def test_get_balance_api_error(self, paper_client, mock_paper_ctx):
        """잔고 조회 실패 시에도 계좌 정보가 포함된다."""
        mock_paper_ctx.stock_query_service.handle_get_account_balance = AsyncMock(
            return_value=make_error_response("잔고 조회 실패")
        )

        resp = paper_client.get("/api/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"
        assert "account_info" in body


# ============================================================================
# 5. 매수 주문 (POST /api/order - buy)
# ============================================================================

class TestBuyOrderPaper:
    def test_buy_order_success(self, paper_client, mock_paper_ctx):
        """매수 주문 성공 시 수동매매 기록도 생성된다."""
        order_result = {"ord_no": "1234567890"}
        mock_paper_ctx.order_execution_service.handle_buy_stock = AsyncMock(
            return_value=make_success_response(order_result)
        )

        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"

        # 서비스 호출 검증
        mock_paper_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with(
            "005930", "10", "70000"
        )
        # 수동매매 기록 검증
        mock_paper_ctx.virtual_manager.log_buy.assert_called_once_with("수동매매", "005930", 70000)

    def test_buy_order_failure(self, paper_client, mock_paper_ctx):
        """매수 주문 실패 시 가상 매매 기록이 생성되지 않는다."""
        mock_paper_ctx.order_execution_service.handle_buy_stock = AsyncMock(
            return_value=make_error_response("주문 실패")
        )

        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "1"
        mock_paper_ctx.virtual_manager.log_buy.assert_not_called()

    def test_buy_order_market_price(self, paper_client, mock_paper_ctx):
        """시장가 매수 (price=0) 시 현재가를 조회하여 기록한다."""
        order_result = {"ord_no": "1234567890"}
        mock_paper_ctx.order_execution_service.handle_buy_stock = AsyncMock(
            return_value=make_success_response(order_result)
        )
        # 시장가일 때 현재가 조회
        mock_paper_ctx.stock_query_service.handle_get_current_stock_price = AsyncMock(
            return_value=make_success_response({"price": "71000"})
        )

        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "0", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"

        # 현재가(71000)로 기록되어야 함
        mock_paper_ctx.virtual_manager.log_buy.assert_called_once_with("수동매매", "005930", 71000)


# ============================================================================
# 6. 매도 주문 (POST /api/order - sell)
# ============================================================================

class TestSellOrderPaper:
    def test_sell_order_success(self, paper_client, mock_paper_ctx):
        """매도 주문 성공 시 수동매매 매도 기록이 생성된다."""
        order_result = {"ord_no": "S123456789"}
        mock_paper_ctx.order_execution_service.handle_sell_stock = AsyncMock(
            return_value=make_success_response(order_result)
        )

        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == "0"

        mock_paper_ctx.order_execution_service.handle_sell_stock.assert_awaited_once_with(
            "005930", "5", "71000"
        )
        mock_paper_ctx.virtual_manager.log_sell.assert_called_once_with("005930", 71000)

    def test_sell_order_failure(self, paper_client, mock_paper_ctx):
        """매도 주문 실패 시 가상 매매 기록이 생성되지 않는다."""
        mock_paper_ctx.order_execution_service.handle_sell_stock = AsyncMock(
            return_value=make_error_response("주문 실패")
        )

        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "1"
        mock_paper_ctx.virtual_manager.log_sell.assert_not_called()

    def test_order_invalid_side(self, paper_client, mock_paper_ctx):
        """잘못된 side 값이면 400 에러를 반환한다."""
        resp = paper_client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "invalid"
        })
        assert resp.status_code == 400


# ============================================================================
# 7. 랭킹 조회 (GET /api/ranking/{category})
# ============================================================================

class TestRankingPaper:
    @pytest.mark.parametrize("category", ["rise", "fall", "volume"])
    def test_get_ranking_success(self, paper_client, mock_paper_ctx, category):
        """상승/하락/거래량 랭킹 조회 성공."""
        ranking_items = [
            MagicMock(to_dict=lambda: {"rank": "1", "name": "삼성전자", "code": "005930"}),
            MagicMock(to_dict=lambda: {"rank": "2", "name": "SK하이닉스", "code": "000660"}),
        ]
        mock_paper_ctx.stock_query_service.handle_get_top_stocks = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=ranking_items)
        )

        resp = paper_client.get(f"/api/ranking/{category}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == ErrorCode.SUCCESS.value
        assert len(body["data"]) == 2
        mock_paper_ctx.stock_query_service.handle_get_top_stocks.assert_awaited_once_with(category)

    def test_get_ranking_invalid_category(self, paper_client, mock_paper_ctx):
        """잘못된 category면 400 에러를 반환한다."""
        resp = paper_client.get("/api/ranking/invalid_category")
        assert resp.status_code == 400

    @pytest.mark.parametrize("category", [
        "trading_value", "foreign_buy", "foreign_sell",
        "inst_buy", "inst_sell", "prsn_buy", "prsn_sell",
        "program_buy", "program_sell",
    ])
    def test_get_ranking_all_valid_categories(self, paper_client, mock_paper_ctx, category):
        """모든 유효한 category가 정상 처리된다."""
        mock_paper_ctx.stock_query_service.handle_get_top_stocks = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[])
        )

        resp = paper_client.get(f"/api/ranking/{category}")
        assert resp.status_code == 200


# ============================================================================
# 8. 시가총액 조회 (GET /api/top-market-cap)
# ============================================================================

class TestMarketCapPaper:
    def test_get_top_market_cap_success(self, paper_client, mock_paper_ctx):
        """시가총액 상위 종목 조회 성공."""
        items = [
            MagicMock(
                hts_kor_isnm="삼성전자", mksc_shrn_iscd="005930",
                stck_prpr="70500", prdy_ctrt="1.73", stck_avls="4200000",
            ),
        ]
        # isinstance(item, dict)가 False가 되도록 dict mock 해제
        for item in items:
            type(item).__instancecheck__ = lambda self, instance: False

        mock_paper_ctx.broker.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=items)
        )

        resp = paper_client.get("/api/top-market-cap?limit=10&market=0001")
        assert resp.status_code == 200

        body = resp.json()
        assert body["rt_cd"] == ErrorCode.SUCCESS.value
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "삼성전자"
        assert body["data"][0]["rank"] == "1"

    def test_get_top_market_cap_invalid_market_defaults(self, paper_client, mock_paper_ctx):
        """잘못된 market 코드는 0001(코스피)로 기본값 처리된다."""
        mock_paper_ctx.broker.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[])
        )

        resp = paper_client.get("/api/top-market-cap?market=9999")
        assert resp.status_code == 200
        mock_paper_ctx.broker.get_top_market_cap_stocks_code.assert_awaited_once_with("0001", 20)


# ============================================================================
# 9. 기술 지표 (GET /api/indicator/*)
# ============================================================================

class TestIndicatorsPaper:
    def test_get_bollinger_bands(self, paper_client, mock_paper_ctx):
        """볼린저 밴드 조회."""
        mock_paper_ctx.indicator_service.get_bollinger_bands = AsyncMock(
            return_value=make_success_response([{"date": "20260307", "upper": 72000, "middle": 70000, "lower": 68000}])
        )

        resp = paper_client.get("/api/indicator/bollinger/005930?period=20&std_dev=2.0")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"
        mock_paper_ctx.indicator_service.get_bollinger_bands.assert_awaited_once_with("005930", 20, 2.0)

    def test_get_rsi(self, paper_client, mock_paper_ctx):
        """RSI 조회."""
        mock_paper_ctx.indicator_service.get_rsi = AsyncMock(
            return_value=make_success_response([{"date": "20260307", "rsi": 65.5}])
        )

        resp = paper_client.get("/api/indicator/rsi/005930?period=14")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"
        mock_paper_ctx.indicator_service.get_rsi.assert_awaited_once_with("005930", 14)

    def test_get_moving_average(self, paper_client, mock_paper_ctx):
        """이동평균선 조회."""
        mock_paper_ctx.indicator_service.get_moving_average = AsyncMock(
            return_value=make_success_response([{"date": "20260307", "ma": 70000}])
        )

        resp = paper_client.get("/api/indicator/ma/005930?period=20&method=sma")
        assert resp.status_code == 200
        assert resp.json()["rt_cd"] == "0"
        mock_paper_ctx.indicator_service.get_moving_average.assert_awaited_once_with("005930", 20, "sma")


# ============================================================================
# 10. 환경 전환 (POST /api/environment)
# ============================================================================

class TestEnvironmentPaper:
    def test_change_to_real_environment(self, paper_client, mock_paper_ctx):
        """모의 → 실전 환경 전환 성공."""
        mock_paper_ctx.get_env_type.return_value = "실전투자"

        resp = paper_client.post("/api/environment", json={"is_paper": False})
        assert resp.status_code == 200

        body = resp.json()
        assert body["success"] is True
        assert body["env_type"] == "실전투자"
        mock_paper_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=False)

    def test_environment_change_failure(self, paper_client, mock_paper_ctx):
        """환경 전환 실패 시 500 에러."""
        mock_paper_ctx.initialize_services = AsyncMock(return_value=False)

        resp = paper_client.post("/api/environment", json={"is_paper": False})
        assert resp.status_code == 500


# ============================================================================
# 11. 서비스 미초기화 시 503 반환
# ============================================================================

class TestUninitializedPaper:
    def test_503_when_context_not_set(self, web_app):
        """WebAppContext가 설정되지 않으면 503을 반환한다."""
        import view.web.api_common as api_common
        from fastapi.testclient import TestClient
        api_common.set_ctx(None)

        with TestClient(web_app) as client:
            resp = client.get("/api/status")
            assert resp.status_code == 503


# ============================================================================
# 12. WebAppContext 초기화 통합 테스트
# ============================================================================

class TestWebAppContextInitialization:
    """
    WebAppContext의 실제 초기화 체인을 검증한다.
    load_config_and_env → initialize_services → initialize_scheduler 흐름에서
    네트워크 호출(토큰 발급)만 mock하고, 서비스 객체가 올바른 타입으로 생성되는지 확인.
    """

    @pytest.fixture
    def mock_config(self):
        """테스트용 config 데이터 (load_configs 반환값 모사)."""
        from config.config_loader import AppConfig
        return AppConfig(**{
            "api_key": "test-real-key",
            "api_secret_key": "test-real-secret",
            "stock_account_number": "12345678-01",
            "url": "https://openapi.koreainvestment.com:9443",
            "websocket_url": "ws://ops.koreainvestment.com:21000",
            "paper_api_key": "test-paper-key",
            "paper_api_secret_key": "test-paper-secret",
            "paper_stock_account_number": "99887766-01",
            "paper_url": "https://openapivts.koreainvestment.com:29443",
            "paper_websocket_url": "ws://ops.koreainvestment.com:31000",
            "is_paper_trading": True,
            "htsid": "test-htsid",
            "custtype": "P",
            "market_open_time": "09:00",
            "market_close_time": "15:30",
            "market_timezone": "Asia/Seoul",
            "web": {"host": "0.0.0.0", "port": 8000},
            "tr_ids": {
                "quotations": {"inquire_price": "FHKST01010100", "market_cap": "FHPST01740000"},
                "account": {"inquire_balance_real": "TTTC8434R", "inquire_balance_paper": "VTTC8434R"},
                "trading": {"order_cash_buy_real": "TTTC0012U", "order_cash_sell_real": "TTTC0011U",
                            "order_cash_buy_paper": "VTTC0012U", "order_cash_sell_paper": "VTTC0011U"},
            },
            "paths": {
                "inquire_price": "/uapi/domestic-stock/v1/quotations/inquire-price",
                "market_cap": "/uapi/domestic-stock/v1/ranking/market-cap",
                "inquire_balance": "/uapi/domestic-stock/v1/trading/inquire-balance",
                "order_cash": "/uapi/domestic-stock/v1/trading/order-cash",
                "hashkey": "/uapi/hashkey",
                "search_info": "/uapi/domestic-stock/v1/quotations/search-info",
                "asking_price": "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                "time_conclude": "/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion",
                "search_stock": "/uapi/domestic-stock/v1/quotations/search-stock-info",
                "ranking_fluctuation": "/uapi/domestic-stock/v1/ranking/fluctuation",
                "ranking_volume": "/uapi/domestic-stock/v1/quotations/volume-rank",
                "ranking_foreign": "/uapi/domestic-stock/v1/quotations/inquire-foreign",
                "investor_trade_by_stock_daily": "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
                "program_trade_by_stock_daily": "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
                "item_news": "/uapi/domestic-stock/v1/quotations/news-title",
                "etf_info": "/uapi/etfetn/v1/quotations/inquire-price",
                "multi_price": "/uapi/domestic-stock/v1/quotations/intstock-multprice",
                "inquire_daily_itemchartprice": "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "inquire_time_itemchartprice": "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                "inquire_time_daily_itemchartprice": "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
                "financial_ratio": "/uapi/domestic-stock/v1/finance/financial-ratio",
                "inquire_conclusion": "/uapi/domestic-stock/v1/quotations/inquire-ccnl",
                "approval_key": "/oauth2/Approval",
                "real_time_price": "/tryitout/H0STCNT0",
            },
            "params": {"fid_div_cls_code": "2"},
        })

    @pytest.mark.asyncio
    async def test_initialize_services_paper_creates_all_services(self, mock_config, test_logger):
        """
        모의투자 모드로 initialize_services 호출 시
        모든 서비스 객체가 올바른 타입으로 생성되는지 검증.
        """
        from view.web.web_app_initializer import WebAppContext
        from brokers.broker_api_wrapper import BrokerAPIWrapper
        from services.trading_service import TradingService
        from services.stock_query_service import StockQueryService
        from services.order_execution_service import OrderExecutionService
        from services.indicator_service import IndicatorService
        from services.oneil_universe_service import OneilUniverseService
        from services.background_service import BackgroundService
        from core.time_manager import TimeManager
        from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            # load_config_and_env 결과 검증
            assert ctx.env is not None
            assert isinstance(ctx.env, KoreaInvestApiEnv)
            assert ctx.time_manager is not None
            assert isinstance(ctx.time_manager, TimeManager)

            # 토큰 발급 mock (네트워크 호출 차단)
            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value="mock-paper-token")
            ctx.env._token_manager_real = MagicMock()
            ctx.env._token_manager_real.get_access_token = AsyncMock(return_value="mock-real-token")

            # initialize_services 실행
            result = await ctx.initialize_services(is_paper_trading=True)

            # 초기화 성공 검증
            assert result is True
            assert ctx.initialized is True
            assert ctx.env.is_paper_trading is True

            # 서비스 타입 검증
            assert isinstance(ctx.broker, BrokerAPIWrapper)
            assert isinstance(ctx.trading_service, TradingService)
            assert isinstance(ctx.stock_query_service, StockQueryService)
            assert isinstance(ctx.order_execution_service, OrderExecutionService)
            assert isinstance(ctx.indicator_service, IndicatorService)
            assert isinstance(ctx.oneil_universe_service, OneilUniverseService)
            assert isinstance(ctx.background_service, BackgroundService)

            # DI 연결 검증: IndicatorService ↔ StockQueryService 순환 주입
            assert ctx.indicator_service.stock_query_service is ctx.stock_query_service

            # get_env_type 검증
            assert ctx.get_env_type() == "모의투자"

    @pytest.mark.asyncio
    async def test_initialize_services_real_creates_all_services(self, mock_config, test_logger):
        """
        실전투자 모드로 initialize_services 호출 시 서비스가 정상 생성되는지 검증.
        """
        from view.web.web_app_initializer import WebAppContext
        from brokers.broker_api_wrapper import BrokerAPIWrapper

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value="mock-paper-token")
            ctx.env._token_manager_real = MagicMock()
            ctx.env._token_manager_real.get_access_token = AsyncMock(return_value="mock-real-token")

            result = await ctx.initialize_services(is_paper_trading=False)

            assert result is True
            assert ctx.initialized is True
            assert ctx.env.is_paper_trading is False
            assert ctx.get_env_type() == "실전투자"
            assert isinstance(ctx.broker, BrokerAPIWrapper)

    @pytest.mark.asyncio
    async def test_initialize_services_token_failure_returns_false(self, mock_config, test_logger):
        """토큰 발급 실패 시 initialize_services가 False를 반환한다."""
        from view.web.web_app_initializer import WebAppContext

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            # 토큰 발급 실패 mock
            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value=None)

            result = await ctx.initialize_services(is_paper_trading=True)

            assert result is False
            assert ctx.initialized is False

    @pytest.mark.asyncio
    async def test_initialize_scheduler_registers_strategies(self, mock_config, test_logger):
        """
        initialize_scheduler 호출 시 스케줄러가 생성되고
        7개 전략이 등록되는지 검증.
        """
        from view.web.web_app_initializer import WebAppContext
        from scheduler.strategy_scheduler import StrategyScheduler

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value="mock-paper-token")
            ctx.env._token_manager_real = MagicMock()
            ctx.env._token_manager_real.get_access_token = AsyncMock(return_value="mock-real-token")

            await ctx.initialize_services(is_paper_trading=True)

            # 스케줄러 초기화
            ctx.initialize_scheduler()

            assert ctx.scheduler is not None
            assert isinstance(ctx.scheduler, StrategyScheduler)

            # 등록된 전략 개수 검증 (7개: VolumeBreakoutLive, ProgramBuyFollow,
            # TraditionalVolumeBreakout, OneilSqueezeBreakout, OneilPocketPivot, HighTightFlag, FirstPullback)
            registered = ctx.scheduler.get_status()
            assert len(registered["strategies"]) == 7

    @pytest.mark.asyncio
    async def test_initialize_then_switch_environment(self, mock_config, test_logger):
        """
        모의투자로 초기화 후 실전으로 재초기화 시
        서비스가 새로 생성되고 환경이 전환되는지 검증.
        """
        from view.web.web_app_initializer import WebAppContext

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value="mock-paper-token")
            ctx.env._token_manager_real = MagicMock()
            ctx.env._token_manager_real.get_access_token = AsyncMock(return_value="mock-real-token")

            # 1차 초기화: 모의투자
            await ctx.initialize_services(is_paper_trading=True)
            assert ctx.get_env_type() == "모의투자"
            paper_broker = ctx.broker

            # 2차 초기화: 실전투자
            await ctx.initialize_services(is_paper_trading=False)
            assert ctx.get_env_type() == "실전투자"
            assert ctx.broker is not paper_broker  # 새 인스턴스 생성 확인

    @pytest.mark.asyncio
    async def test_full_startup_flow_paper(self, mock_config, test_logger, web_app):
        """
        전체 시작 흐름 통합 테스트:
        WebAppContext 초기화 → 서비스 생성 → set_ctx → TestClient로 API 호출 성공.
        """
        from view.web.web_app_initializer import WebAppContext
        from fastapi.testclient import TestClient
        import view.web.api_common as api_common

        class SimpleContext:
            env = None

        with patch("config.config_loader.load_configs", return_value=mock_config), \
             patch("view.web.web_app_initializer.VirtualTradeManager"), \
             patch("view.web.web_app_initializer.StockCodeMapper"):

            ctx = WebAppContext(SimpleContext())
            ctx.logger = test_logger
            ctx.load_config_and_env()

            ctx.env._token_manager_paper = MagicMock()
            ctx.env._token_manager_paper.get_access_token = AsyncMock(return_value="mock-paper-token")
            ctx.env._token_manager_real = MagicMock()
            ctx.env._token_manager_real.get_access_token = AsyncMock(return_value="mock-real-token")

            await ctx.initialize_services(is_paper_trading=True)

            # 실제 ctx를 FastAPI 앱에 연결
            api_common.set_ctx(ctx)
            try:
                with TestClient(web_app) as client:
                    # /api/status는 실제 WebAppContext 메서드를 호출
                    resp = client.get("/api/status")
                    assert resp.status_code == 200

                    body = resp.json()
                    assert body["initialized"] is True
                    assert body["env_type"] == "모의투자"
            finally:
                api_common.set_ctx(None)
