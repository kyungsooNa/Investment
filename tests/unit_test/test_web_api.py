import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from common.types import ResCommonResponse

def test_get_status(web_client, mock_web_ctx):
    """GET /api/status 엔드포인트 테스트"""
    response = web_client.get("/api/status")
    
    assert response.status_code == 200
    assert response.json() == {
        "market_open": True,
        "env_type": "모의투자",
        "current_time": "2025-01-01 12:00:00",
        "initialized": True
    }

@pytest.mark.asyncio
async def test_get_stock_price(web_client, mock_web_ctx):
    """GET /api/stock/{code} 엔드포인트 테스트"""
    
    # Service 응답 Mocking
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"code": "005930", "price": 70000}
    )
    
    response = web_client.get("/api/stock/005930")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert json_resp["data"]["price"] == 70000
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_place_order_buy(web_client, mock_web_ctx):
    """POST /api/order (매수) 엔드포인트 테스트"""
    
    # 주문 성공 응답 Mocking
    mock_web_ctx.order_execution_service.handle_buy_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )
    
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "buy"}
    response = web_client.post("/api/order", json=payload)
    
    assert response.status_code == 200
    assert response.json()["data"]["ord_no"] == "12345"
    
    # 서비스 호출 및 가상 매매 기록 확인
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with("005930", "10", "70000")
    mock_web_ctx.virtual_manager.log_buy.assert_called_once()

def test_login_success(web_client, mock_web_ctx):
    """POST /api/auth/login 로그인 성공 테스트"""
    response = web_client.post("/api/auth/login", data={"username": "admin", "password": "password"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "access_token" in response.cookies

def test_websocket_echo_endpoint(web_client):
    """WebSocket /api/ws/echo 엔드포인트 테스트"""
    # TestClient의 websocket_connect를 사용하여 연결 (라우터 prefix '/api' 포함)
    with web_client.websocket_connect("/api/ws/echo") as websocket:
        websocket.send_text("테스트 메시지")
        data = websocket.receive_text()
        assert data == "Message text was: 테스트 메시지"

@pytest.mark.asyncio
async def test_get_balance(web_client, mock_web_ctx):
    """GET /api/balance 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"output1": [], "output2": []}
    )
    # 계좌 정보 추출을 위한 env 설정 (mock_web_ctx fixture에서 이미 설정됨)
    
    response = web_client.get("/api/balance")
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    # account_info가 추가되었는지 확인
    assert "account_info" in json_resp
    assert json_resp["account_info"]["type"] == "모의투자"

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
async def test_change_environment(web_client, mock_web_ctx):
    """POST /api/environment 엔드포인트 테스트"""
    mock_web_ctx.initialize_services = AsyncMock(return_value=True)
    mock_web_ctx.get_env_type.return_value = "실전투자"
    
    response = web_client.post("/api/environment", json={"is_paper": False})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["env_type"] == "실전투자"
    mock_web_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=False)

@pytest.mark.asyncio
async def test_virtual_endpoints(web_client, mock_web_ctx):
    """모의투자 관련 엔드포인트 테스트"""
    # Summary
    mock_web_ctx.virtual_manager.get_summary.return_value = {"total_trades": 10}
    response = web_client.get("/api/virtual/summary")
    assert response.status_code == 200
    assert response.json()["total_trades"] == 10

    # Strategies
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StrategyA"]
    response = web_client.get("/api/virtual/strategies")
    assert response.status_code == 200
    assert response.json() == ["StrategyA"]

    # History
    # 1. 가상 매매 데이터 Mocking
    mock_web_ctx.virtual_manager.get_all_trades.return_value = [
        {"code": "005930", "strategy": "StrategyA", "buy_price": 1000, "return_rate": 10.0, "status": "HOLD"}
    ]
    
    # 2. JSON 직렬화 오류 방지를 위해 float 값 반환 설정
    mock_web_ctx.virtual_manager.get_daily_change.return_value = 0.0
    mock_web_ctx.virtual_manager.get_weekly_change.return_value = 0.0
    mock_web_ctx.virtual_manager._load_data.return_value = {}
    mock_web_ctx.virtual_manager.save_daily_snapshot.return_value = None

    # 3. enrichment 로직 Mocking
    mock_web_ctx.stock_code_mapper = MagicMock()
    mock_web_ctx.stock_code_mapper.get_name_by_code.return_value = "삼성전자"
    
    # 4. 현재가 조회 Mocking (AsyncMock)
    mock_web_ctx.stock_query_service.handle_get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={"price": "1100", "rate": "10.0"}
    )
    
    response = web_client.get("/api/virtual/history")
    assert response.status_code == 200
    assert "trades" in response.json()
    assert len(response.json()["trades"]) == 1
    assert response.json()["trades"][0]["stock_name"] == "삼성전자"

@pytest.mark.asyncio
async def test_scheduler_endpoints(web_client, mock_web_ctx):
    """스케줄러 관련 엔드포인트 테스트"""
    # Status
    mock_web_ctx.scheduler.get_status = MagicMock(return_value={"running": False})
    response = web_client.get("/api/scheduler/status")
    assert response.status_code == 200
    assert response.json()["running"] is False

    # Start
    response = web_client.post("/api/scheduler/start")
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_web_ctx.scheduler.start.assert_awaited_once()

    # Stop
    response = web_client.post("/api/scheduler/stop")
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_web_ctx.scheduler.stop.assert_awaited_once()

    # History
    mock_web_ctx.scheduler.get_signal_history = MagicMock(return_value=[])
    response = web_client.get("/api/scheduler/history")
    assert response.status_code == 200
    assert response.json()["history"] == []

@pytest.mark.asyncio
async def test_get_scheduler_history_name_correction(web_client, mock_web_ctx):
    """
    GET /api/scheduler/history 엔드포인트가 잘못된 종목명을 올바르게 보정하는지 테스트.
    스케줄러 이력에 종목명 대신 업종명이 들어간 경우, StockCodeMapper를 통해 최신 종목명으로 덮어쓰는지 확인.
    """
    # 1. Mock 데이터 설정
    # 스케줄러는 '반도체'라는 잘못된 이름으로 이력을 반환
    incorrect_history = [
        {
            "code": "005930",
            "name": "반도체",  # 잘못된 이름 (업종명)
            "action": "BUY",
            "price": 70000,
            "reason": "Test Signal",
            "strategy_name": "TestStrategy",
            "timestamp": "2023-01-01 10:00:00",
            "api_success": True
        }
    ]
    mock_web_ctx.scheduler.get_signal_history.return_value = incorrect_history

    # StockCodeMapper Mock 설정
    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code.return_value = "삼성전자"  # '005930'에 대해 '삼성전자'를 반환
    mock_web_ctx.stock_code_mapper = mock_mapper

    # 2. API 호출
    response = web_client.get("/api/scheduler/history")

    # 3. 검증
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 1
    assert data["history"][0]["name"] == "삼성전자"
    mock_mapper.get_name_by_code.assert_called_once_with("005930")

@pytest.mark.asyncio
async def test_get_strategy_chart(web_client, mock_web_ctx):
    """GET /api/virtual/chart/{strategy_name} 엔드포인트 테스트"""
    # 1. 가상 매매 매니저 Mocking (전략 히스토리 반환)
    mock_web_ctx.virtual_manager.get_all_strategies.return_value = ["StrategyA"]
    mock_web_ctx.virtual_manager.get_strategy_return_history.return_value = [
        {"date": "2025-01-01", "return_rate": 1.0},
        {"date": "2025-01-02", "return_rate": 2.0}
    ]
    
    # 2. 벤치마크(KODEX 200) 데이터 조회 Mocking
    # stock_query_service.trading_service.get_ohlcv_range 호출 모의
    mock_web_ctx.stock_query_service.trading_service.get_ohlcv_range.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[
            {"date": "20250101", "close": 30000},
            {"date": "20250102", "close": 30300} # 1% 상승
        ]
    )

    response = web_client.get("/api/virtual/chart/StrategyA")
    assert response.status_code == 200
    data = response.json()
    assert "histories" in data
    assert "benchmarks" in data
    assert "StrategyA" in data["histories"]
    assert "KOSPI200" in data["benchmarks"]
    assert len(data["benchmarks"]["KOSPI200"]) == 2

@pytest.mark.asyncio
async def test_get_bollinger_bands(web_client, mock_web_ctx):
    """GET /api/indicator/bollinger/{code} 엔드포인트 테스트"""
    from common.types import ResBollingerBand
    
    # indicator_service Mock 설정 (conftest.py의 mock_web_ctx에 기본적으로 포함되지 않았을 수 있으므로 추가)
    mock_web_ctx.indicator_service = AsyncMock()
    
    mock_band = ResBollingerBand(
        code="005930", date="20250101", close=70000.0,
        middle=69000.0, upper=71000.0, lower=67000.0
    )
    
    mock_web_ctx.indicator_service.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[mock_band]
    )
    
    response = web_client.get("/api/indicator/bollinger/005930?period=20&std_dev=2.0")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert isinstance(json_resp["data"], list)
    assert json_resp["data"][0]["code"] == "005930"
    assert json_resp["data"][0]["upper"] == 71000.0
    
    mock_web_ctx.indicator_service.get_bollinger_bands.assert_awaited_once_with("005930", 20, 2.0)

@pytest.mark.asyncio
async def test_get_rsi(web_client, mock_web_ctx):
    """GET /api/indicator/rsi/{code} 엔드포인트 테스트"""
    from common.types import ResRSI
    
    # indicator_service Mock 설정
    mock_web_ctx.indicator_service = AsyncMock()
    
    mock_rsi = ResRSI(code="005930", date="20250101", close=70000.0, rsi=65.5)
    
    mock_web_ctx.indicator_service.get_rsi.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=mock_rsi
    )
    
    response = web_client.get("/api/indicator/rsi/005930?period=14")
    
    assert response.status_code == 200
    assert response.json()["data"]["rsi"] == 65.5
    mock_web_ctx.indicator_service.get_rsi.assert_awaited_once_with("005930", 14)

def test_login_failure(web_client, mock_web_ctx):
    """POST /api/auth/login 로그인 실패 테스트"""
    response = web_client.post("/api/auth/login", data={"username": "wrong", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["success"] is False

def test_get_ctx_uninitialized(web_client):
    """서비스 초기화 전 호출 시 503 에러 테스트"""
    from view.web import web_api
    original_ctx = web_api._ctx
    web_api.set_ctx(None)
    
    try:
        # get_status calls _get_ctx which raises 503
        response = web_client.get("/api/status")
        assert response.status_code == 503
    finally:
        web_api.set_ctx(original_ctx)

@pytest.mark.asyncio
async def test_get_stock_chart(web_client, mock_web_ctx):
    """GET /api/chart/{code} 엔드포인트 테스트"""
    mock_web_ctx.stock_query_service.get_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"date": "20250101", "close": 100}]
    )
    response = web_client.get("/api/chart/005930?period=D")
    assert response.status_code == 200
    assert response.json()["data"][0]["close"] == 100

@pytest.mark.asyncio
async def test_get_moving_average(web_client, mock_web_ctx):
    """GET /api/indicator/ma/{code} 엔드포인트 테스트"""
    # indicator_service가 mock_web_ctx에 없을 수 있으므로 확인 및 설정
    if not hasattr(mock_web_ctx, 'indicator_service') or not isinstance(mock_web_ctx.indicator_service, AsyncMock):
        mock_web_ctx.indicator_service = AsyncMock()
        
    mock_web_ctx.indicator_service.get_moving_average.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data=[{"ma": 100}]
    )
    response = web_client.get("/api/indicator/ma/005930")
    assert response.status_code == 200
    assert response.json()["data"][0]["ma"] == 100

@pytest.mark.asyncio
async def test_place_order_sell(web_client, mock_web_ctx):
    """POST /api/order (매도) 엔드포인트 테스트"""
    mock_web_ctx.order_execution_service.handle_sell_stock.return_value = ResCommonResponse(
        rt_cd="0", msg1="Order Placed", data={"ord_no": "12345"}
    )
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "sell"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 200
    mock_web_ctx.order_execution_service.handle_sell_stock.assert_awaited_once()
    mock_web_ctx.virtual_manager.log_sell.assert_called_once()

@pytest.mark.asyncio
async def test_place_order_invalid_side(web_client, mock_web_ctx):
    """POST /api/order 잘못된 side 테스트"""
    payload = {"code": "005930", "price": "70000", "qty": "10", "side": "invalid"}
    response = web_client.post("/api/order", json=payload)
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_change_environment_failure(web_client, mock_web_ctx):
    """POST /api/environment 실패 테스트"""
    mock_web_ctx.initialize_services = AsyncMock(return_value=False)
    response = web_client.post("/api/environment", json={"is_paper": True})
    assert response.status_code == 500

@pytest.mark.asyncio
async def test_program_trading_endpoints(web_client, mock_web_ctx):
    """프로그램 매매 관련 엔드포인트 테스트"""
    # realtime_data_manager Mock 설정
    mock_web_ctx.realtime_data_manager = MagicMock()
    
    # Subscribe
    mock_web_ctx.start_program_trading = AsyncMock(return_value=True)
    mock_web_ctx.realtime_data_manager.get_subscribed_codes.return_value = ["005930"]
    
    resp = web_client.post("/api/program-trading/subscribe", json={"code": "005930"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    
    # Status
    resp = web_client.get("/api/program-trading/status")
    assert resp.status_code == 200
    assert resp.json()["subscribed"] is True
    
    # History
    mock_web_ctx.stock_query_service.handle_get_program_trading_history.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={}
    )
    resp = web_client.get("/api/program-trading/history/005930")
    assert resp.status_code == 200
    
    # Unsubscribe
    mock_web_ctx.stop_program_trading = AsyncMock()
    resp = web_client.post("/api/program-trading/unsubscribe", json={"code": "005930"})
    assert resp.status_code == 200
    mock_web_ctx.stop_program_trading.assert_awaited_with("005930")

@pytest.mark.asyncio
async def test_scheduler_strategy_control(web_client, mock_web_ctx):
    """스케줄러 개별 전략 제어 테스트"""
    # Start Strategy
    mock_web_ctx.scheduler.start_strategy = AsyncMock(return_value=True)
    mock_web_ctx.scheduler.get_status = MagicMock(return_value={})
    
    resp = web_client.post("/api/scheduler/strategy/TestStrat/start")
    assert resp.status_code == 200
    mock_web_ctx.scheduler.start_strategy.assert_awaited_with("TestStrat")
    
    # Stop Strategy
    mock_web_ctx.scheduler.stop_strategy = MagicMock(return_value=True)
    resp = web_client.post("/api/scheduler/strategy/TestStrat/stop")
    assert resp.status_code == 200
    mock_web_ctx.scheduler.stop_strategy.assert_called_with("TestStrat")

@pytest.mark.asyncio
async def test_get_balance_fallback_env(web_client, mock_web_ctx):
    """GET /api/balance 환경 설정 폴백 테스트"""
    # ctx.env가 없을 때 broker.env를 사용하는지 확인
    mock_web_ctx.stock_query_service.handle_get_account_balance.return_value = ResCommonResponse(
        rt_cd="0", msg1="Success", data={}
    )
    
    # ctx.env 제거 (임시)
    original_env = mock_web_ctx.env
    del mock_web_ctx.env
    
    # broker.env 설정
    mock_web_ctx.broker.env = MagicMock()
    mock_web_ctx.broker.env.active_config = {"stock_account_number": "9999"}
    mock_web_ctx.broker.env.is_paper_trading = True
    
    try:
        response = web_client.get("/api/balance")
        assert response.status_code == 200
        assert response.json()["account_info"]["number"] == "9999"
    finally:
        # 복구
        mock_web_ctx.env = original_env