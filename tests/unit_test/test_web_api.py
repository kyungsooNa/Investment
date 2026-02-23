import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from view.web import web_api
from common.types import ResCommonResponse, ErrorCode

# 테스트용 FastAPI 앱 생성 및 라우터 등록
app = FastAPI()
app.include_router(web_api.router)

@pytest.fixture
def client():
    """FastAPI TestClient 픽스처"""
    return TestClient(app)

@pytest.fixture
async def async_client():
    """비동기 스트리밍 테스트를 위한 httpx AsyncClient 픽스처"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest.fixture
def mock_ctx():
    """
    WebAPI가 의존하는 WebAppContext를 모킹하여 주입합니다.
    실제 서비스 로직이나 DB/API 호출 없이 컨트롤러 로직만 테스트할 수 있게 합니다.
    """
    ctx = MagicMock()
    
    # 기본 상태 설정
    ctx.is_market_open.return_value = True
    ctx.get_env_type.return_value = "모의투자"
    ctx.get_current_time_str.return_value = "2025-01-01 10:00:00"
    ctx.initialized = True
    
    # 내부 서비스 모킹 (AsyncMock 사용)
    ctx.stock_query_service = AsyncMock()
    ctx.order_execution_service = AsyncMock()
    ctx.broker = AsyncMock()
    ctx.virtual_manager = MagicMock()
    
    # web_api 모듈의 전역 변수 _ctx에 모킹된 객체 주입
    web_api.set_ctx(ctx)
    return ctx

def test_get_status(client, mock_ctx):
    """GET /api/status 엔드포인트 테스트"""
    response = client.get("/api/status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["market_open"] is True
    assert data["env_type"] == "모의투자"
    assert data["initialized"] is True

@pytest.mark.asyncio
async def test_get_stock_price(client, mock_ctx):
    """GET /api/stock/{code} 엔드포인트 테스트"""
    # 서비스가 반환할 가짜 응답 설정
    mock_response = ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={"stck_prpr": "70000"}
    )
    mock_ctx.stock_query_service.handle_get_current_stock_price.return_value = mock_response
    
    # API 호출
    response = client.get("/api/stock/005930")
    
    # 검증
    assert response.status_code == 200
    data = response.json()
    assert data["rt_cd"] == "0"
    assert data["data"]["stck_prpr"] == "70000"
    
    # 실제 서비스 메서드가 올바른 인자로 호출되었는지 확인
    mock_ctx.stock_query_service.handle_get_current_stock_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_place_order_buy(client, mock_ctx):
    """POST /api/order 매수 주문 테스트"""
    # 주문 성공 응답 설정
    mock_response = ResCommonResponse(
        rt_cd="0",
        msg1="주문 전송 완료",
        data={"ORD_NO": "12345"}
    )
    mock_ctx.order_execution_service.handle_buy_stock.return_value = mock_response
    
    payload = {
        "code": "005930",
        "price": "70000",
        "qty": "10",
        "side": "buy"
    }
    
    response = client.post("/api/order", json=payload)
    
    assert response.status_code == 200
    assert response.json()["rt_cd"] == "0"
    
    # 주문 서비스 호출 및 가상 매매 기록 호출 검증
    mock_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with("005930", "10", "70000")
    mock_ctx.virtual_manager.log_buy.assert_called_once_with("수동매매", "005930", 70000)

# @pytest.mark.asyncio
# async def test_program_trading_stream_sse(async_client, mock_ctx):
#     """GET /api/program-trading/stream SSE 엔드포인트 테스트"""
    
#     # 1. Context의 큐 리스트를 실제 리스트로 초기화 (Mock 객체 대신)
#     #    엔드포인트가 이 리스트에 큐를 append 할 수 있어야 함
#     mock_ctx._pt_queues = []

#     # 2. SSE 스트림 연결 시작
#     async with async_client.stream("GET", "/api/program-trading/stream") as response:
#         assert response.status_code == 200
        
#         # 큐 등록 대기 (비동기 타이밍 이슈 방지)
#         for _ in range(10):
#             if mock_ctx._pt_queues:
#                 break
#             await asyncio.sleep(0.1)

#         # 연결 후 엔드포인트가 큐를 생성해서 등록했는지 확인
#         assert len(mock_ctx._pt_queues) == 1
#         queue = mock_ctx._pt_queues[0]
        
#         # 3. 테스트용 데이터 주입 (서버 내부 동작 시뮬레이션)
#         test_data = {"code": "005930", "price": "70000", "msg": "프로그램 매수"}
#         await queue.put(test_data)
        
#         # 4. 스트림에서 데이터 읽기 및 검증
#         #    타임아웃을 적용하여 무한 대기 방지
#         async def read_stream():
#             async for line in response.aiter_lines():
#                 if line.startswith("data:"):
#                     return json.loads(line.replace("data: ", ""))
        
#         received_json = await asyncio.wait_for(read_stream(), timeout=2.0)
#         assert received_json == test_data

#         # 5. 서버 루프 종료를 위해 None 전송 (Poison Pill)
#         #    이것이 없으면 서버의 while True 루프가 끝나지 않아 테스트가 hang 걸릴 수 있음
#         await queue.put(None)

def test_websocket_echo_endpoint(client):
    """WebSocket /api/ws/echo 엔드포인트 테스트"""
    # TestClient의 websocket_connect를 사용하여 연결 (라우터 prefix '/api' 포함)
    with client.websocket_connect("/api/ws/echo") as websocket:
        websocket.send_text("테스트 메시지")
        data = websocket.receive_text()
        assert data == "Message text was: 테스트 메시지"