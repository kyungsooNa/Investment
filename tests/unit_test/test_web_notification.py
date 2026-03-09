# tests/unit_test/test_web_notification.py

import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from view.web.routes.notification import router, stream_notifications

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    return app

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.notification_manager = MagicMock()
    return ctx

@patch("view.web.routes.notification._get_ctx")
def test_get_recent_notifications(mock_get_ctx, client, mock_ctx):
    """최근 알림 목록 조회 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    mock_notifications = [
        {"id": 1, "message": "test1", "category": "info"},
        {"id": 2, "message": "test2", "category": "error"}
    ]
    mock_ctx.notification_manager.get_recent.return_value = mock_notifications

    # 1. 기본 호출
    response = client.get("/notifications/recent")
    assert response.status_code == 200
    assert response.json() == {"notifications": mock_notifications}
    mock_ctx.notification_manager.get_recent.assert_called_with(count=50, category=None)

    # 2. 파라미터 전달 호출
    response = client.get("/notifications/recent?count=10&category=info")
    assert response.status_code == 200
    mock_ctx.notification_manager.get_recent.assert_called_with(count=10, category="info")

@pytest.mark.asyncio
@patch("view.web.routes.notification._get_ctx")
async def test_stream_notifications_data(mock_get_ctx, mock_ctx):
    """SSE 스트리밍 데이터 전송 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    queue = asyncio.Queue()
    mock_ctx.notification_manager.create_subscriber_queue.return_value = queue
    
    mock_request = MagicMock(spec=Request)
    mock_request.is_disconnected = AsyncMock(return_value=False)
    
    # 데이터 주입
    test_data = {"msg": "hello"}
    await queue.put(test_data)
    await queue.put(None) # 종료 신호
    
    response = await stream_notifications(mock_request)
    iterator = response.body_iterator
    
    # 첫 번째 데이터 확인
    item = await iterator.__anext__()
    assert item == f"data: {json.dumps(test_data, ensure_ascii=False)}\n\n"
    
    # 종료 확인
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
        
    mock_ctx.notification_manager.create_subscriber_queue.assert_called_once()
    mock_ctx.notification_manager.remove_subscriber_queue.assert_called_once_with(queue)

@pytest.mark.asyncio
@patch("view.web.routes.notification._get_ctx")
async def test_stream_notifications_timeout_keepalive(mock_get_ctx, mock_ctx):
    """SSE 스트리밍 타임아웃 및 keepalive 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    queue = asyncio.Queue()
    mock_ctx.notification_manager.create_subscriber_queue.return_value = queue
    
    mock_request = MagicMock(spec=Request)
    # 1. 루프 진입 (False)
    # 2. Timeout 발생 후 연결 확인 (False) -> keepalive 전송
    # 3. 루프 재진입 연결 확인 (True) -> 종료
    mock_request.is_disconnected = AsyncMock(side_effect=[False, False, True])
    
    # asyncio.wait_for가 TimeoutError를 발생시키도록 패치
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        response = await stream_notifications(mock_request)
        iterator = response.body_iterator
        
        # keepalive 메시지 확인
        item = await iterator.__anext__()
        assert item == ": keepalive\n\n"
        
        # 종료 확인
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()

    mock_ctx.notification_manager.remove_subscriber_queue.assert_called_once()

@pytest.mark.asyncio
@patch("view.web.routes.notification._get_ctx")
async def test_stream_notifications_disconnect(mock_get_ctx, mock_ctx):
    """SSE 스트리밍 클라이언트 연결 끊김 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    queue = asyncio.Queue()
    mock_ctx.notification_manager.create_subscriber_queue.return_value = queue
    
    mock_request = MagicMock(spec=Request)
    # 바로 연결 끊김 상태
    mock_request.is_disconnected = AsyncMock(return_value=True)
    
    response = await stream_notifications(mock_request)
    iterator = response.body_iterator
    
    # 데이터 없이 바로 종료되어야 함
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()
        
    mock_ctx.notification_manager.remove_subscriber_queue.assert_called_once()

@pytest.mark.asyncio
@patch("view.web.routes.notification._get_ctx")
async def test_stream_notifications_cancelled(mock_get_ctx, mock_ctx):
    """SSE 스트리밍 CancelledError 처리 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    queue = asyncio.Queue()
    mock_ctx.notification_manager.create_subscriber_queue.return_value = queue
    
    mock_request = MagicMock(spec=Request)
    mock_request.is_disconnected = AsyncMock(return_value=False)
    
    # wait_for에서 CancelledError 발생 시뮬레이션
    with patch("asyncio.wait_for", side_effect=asyncio.CancelledError):
        response = await stream_notifications(mock_request)
        iterator = response.body_iterator
        
        # CancelledError는 내부에서 catch하고 pass하므로 반복 종료
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()
            
    mock_ctx.notification_manager.remove_subscriber_queue.assert_called_once()

@pytest.mark.asyncio
@patch("view.web.routes.notification._get_ctx")
async def test_stream_notifications_timeout_disconnect_check(mock_get_ctx, mock_ctx):
    """SSE 스트리밍 타임아웃 발생 직후 클라이언트 연결이 끊긴 경우 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    queue = asyncio.Queue()
    mock_ctx.notification_manager.create_subscriber_queue.return_value = queue
    
    mock_request = MagicMock(spec=Request)
    # 1. 루프 진입 시 연결 확인 (False)
    # 2. TimeoutError 발생 후 예외 처리 블록 내 연결 확인 (True) -> break
    mock_request.is_disconnected = AsyncMock(side_effect=[False, True])
    
    # asyncio.wait_for가 TimeoutError를 발생시키도록 패치
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        response = await stream_notifications(mock_request)
        iterator = response.body_iterator
        
        # keepalive 메시지 전송 없이 바로 종료되어야 함 (StopAsyncIteration)
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()

    mock_ctx.notification_manager.remove_subscriber_queue.assert_called_once()
