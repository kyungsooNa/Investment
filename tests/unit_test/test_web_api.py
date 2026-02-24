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
    assert "benchmark" in data
    assert "StrategyA" in data["histories"]
    assert len(data["benchmark"]) == 2

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
        rt_cd="0", msg1="Success", data=mock_band
    )
    
    response = web_client.get("/api/indicator/bollinger/005930?period=20&std_dev=2.0")
    
    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["rt_cd"] == "0"
    assert json_resp["data"]["code"] == "005930"
    assert json_resp["data"]["upper"] == 71000.0
    
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