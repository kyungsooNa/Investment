import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import json
import asyncio

@pytest.mark.asyncio
async def test_subscribe_program_trading(web_client, mock_web_ctx):
    """POST /api/program-trading/subscribe 엔드포인트 테스트"""
    
    # RealtimeDataManager Mock 설정
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm

    # 구독 성공 Mocking
    mock_web_ctx.start_program_trading = AsyncMock(return_value=True)
    mock_rdm.get_subscribed_codes.return_value = ["005930"] # 구독 후 상태 시뮬레이션
    
    response = web_client.post("/api/program-trading/subscribe", json={"code": "005930"})
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "005930" in response.json()["codes"]
    mock_web_ctx.start_program_trading.assert_awaited_once_with("005930")

def test_save_pt_data(web_client, mock_web_ctx):
    """POST /api/program-trading/save-data 엔드포인트 테스트"""
    
    # RealtimeDataManager Mock 설정
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm
    
    data = {
        "chartData": {"005930": {"valueData": []}},
        "subscribedCodes": ["005930"],
        "codeNameMap": {"005930": "삼성전자"}
    }
    
    response = web_client.post("/api/program-trading/save-data", json=data)
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    # 매니저의 save_snapshot 호출 확인
    mock_rdm.save_snapshot.assert_called_once()
    # 인자 검증 (dict로 변환되어 전달됨)
    args, _ = mock_rdm.save_snapshot.call_args
    assert args[0]["subscribedCodes"] == ["005930"]

def test_load_pt_data_success(web_client, mock_web_ctx):
    """GET /api/program-trading/load-data 엔드포인트 테스트 (성공 시)"""
    
    mock_data = {"chartData": {}, "subscribedCodes": []}
    
    # RealtimeDataManager Mock 설정
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm
    mock_rdm.load_snapshot.return_value = mock_data
    
    response = web_client.get("/api/program-trading/load-data")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"] == mock_data
    mock_rdm.load_snapshot.assert_called_once()

def test_load_pt_data_file_not_found(web_client, mock_web_ctx):
    """GET /api/program-trading/load-data 엔드포인트 테스트 (파일 없음)"""
    
    # RealtimeDataManager Mock 설정
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm
    mock_rdm.load_snapshot.return_value = None
    
    response = web_client.get("/api/program-trading/load-data")
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["msg"] == "File not found"

@pytest.mark.asyncio
async def test_stream_program_trading_logic(mock_web_ctx):
    """
    GET /api/program-trading/stream 엔드포인트 로직 테스트.
    다양한 형태의 데이터(한글, 특수문자, 숫자 등)가 SSE 포맷으로 올바르게 변환되는지 검증합니다.
    """
    from view.web.web_api import stream_program_trading
    from fastapi import Request
    
    # 1. 다양한 테스트 데이터 준비
    test_datasets = [
        {
            "유가증권단축종목코드": "005930",
            "주식체결시간": "120000",
            "순매수체결량": "100",
            "순매수거래대금": "7000000"
        },
        {
            "msg": "한글 테스트",
            "val": 12345,
            "float": 3.14,
            "bool": True,
            "none": None
        },
        {
            "special_chars": "!@#$%^&*()_+",
            "nested": {"a": 1, "b": "2"}
        }
    ]

    # 2. Mock Request 설정 (연결 끊김 없음)
    mock_request = AsyncMock(spec=Request)
    mock_request.is_disconnected = AsyncMock(return_value=False)

    # 3. RealtimeDataManager 및 Queue Mocking
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm
    
    # 실제 asyncio.Queue 사용 (테스트 제어용)
    test_queue = asyncio.Queue()
    mock_rdm.create_subscriber_queue.return_value = test_queue
    mock_rdm.get_history_data.return_value = {} # 히스토리 없음 가정

    # 4. 핸들러 호출 (StreamingResponse 반환)
    # _get_ctx()를 mock_web_ctx로 패치하여 테스트 컨텍스트를 사용하도록 함
    with patch("view.web.web_api._get_ctx", return_value=mock_web_ctx):
        response = await stream_program_trading(mock_request)

    iterator = response.body_iterator
    
    # 5. 데이터 주입 및 검증 루프
    for data in test_datasets:
        await test_queue.put(data)
        
        # 제너레이터에서 청크 가져오기
        chunk = await iterator.__anext__()
        
        # SSE 포맷 검증 ("data: ...\n\n")
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        
        # JSON 파싱 및 내용 검증
        json_str = chunk.replace("data: ", "").strip()
        received_data = json.loads(json_str)
        
        # 원본 데이터와 일치하는지 확인
        assert received_data == data

    # 6. 종료 신호 (Poison Pill)
    await test_queue.put(None)
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
        
    # 7. 정리 확인 (finally 블록 실행 확인)
    mock_rdm.remove_subscriber_queue.assert_called_with(test_queue)

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
    from common.types import ResCommonResponse
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
async def test_program_trading_save_load(web_client, mock_web_ctx):
    """프로그램 매매 데이터 저장/로드 테스트"""
    mock_web_ctx.realtime_data_manager = MagicMock()

    # Save
    payload = {"chartData": {}, "subscribedCodes": [], "codeNameMap": {}, "savedAt": "2025-01-01"}
    resp = web_client.post("/api/program-trading/save-data", json=payload)
    assert resp.status_code == 200
    mock_web_ctx.realtime_data_manager.save_snapshot.assert_called()

    # Load
    mock_web_ctx.realtime_data_manager.load_snapshot.return_value = payload
    resp = web_client.get("/api/program-trading/load-data")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_websocket_echo_endpoint(web_client):
    """WebSocket /api/ws/echo 엔드포인트 테스트"""
    with web_client.websocket_connect("/api/ws/echo") as websocket:
        websocket.send_text("테스트 메시지")
        data = websocket.receive_text()
        assert data == "Message text was: 테스트 메시지"


@pytest.mark.asyncio
async def test_stream_program_trading_keepalive(mock_web_ctx):
    """
    GET /api/program-trading/stream 엔드포인트의 Keepalive 동작 테스트.
    데이터가 없을 때 타임아웃 후 keepalive 메시지를 전송하는지 검증합니다.
    """
    from view.web.web_api import stream_program_trading
    from fastapi import Request

    # Mock Request
    mock_request = AsyncMock(spec=Request)
    mock_request.is_disconnected.return_value = False

    # RealtimeDataManager Mock
    mock_rdm = MagicMock()
    mock_web_ctx.realtime_data_manager = mock_rdm
    
    test_queue = asyncio.Queue()
    mock_rdm.create_subscriber_queue.return_value = test_queue
    mock_rdm.get_history_data.return_value = {}

    # 핸들러 호출
    with patch("view.web.web_api._get_ctx", return_value=mock_web_ctx):
        response = await stream_program_trading(mock_request)

    iterator = response.body_iterator

    # 1. 데이터 없이 대기 -> 타임아웃 발생 -> keepalive 메시지 수신 예상
    # stream_program_trading 내부 timeout은 0.1초
    chunk = await iterator.__anext__()
    
    # Keepalive 메시지 포맷 검증 (SSE comment 형식)
    assert chunk == ": keepalive\n\n"

    # 2. 종료 유도
    mock_request.is_disconnected.return_value = True
    
    try:
        # 타임아웃 후 루프 돌면서 종료 체크
        await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    except (StopAsyncIteration, asyncio.TimeoutError):
        pass
        
    mock_rdm.remove_subscriber_queue.assert_called_with(test_queue)