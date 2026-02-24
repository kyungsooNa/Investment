import pytest
import sys
from pathlib import Path

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