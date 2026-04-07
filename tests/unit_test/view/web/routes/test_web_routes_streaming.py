import asyncio
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch

from view.web.routes.streaming import router, stream_stock_price
from services.price_subscription_service import SubscriptionPriority
from repositories.streaming_stock_repo import StreamingType

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
    ctx.price_subscription_service = AsyncMock()
    ctx.price_subscription_service.get_status = MagicMock(
        return_value={"active_count": 2, "active_codes": ["005930", "000660"]}
    )
    return ctx

@patch("view.web.routes.streaming._get_ctx")
def test_subscribe_stock_service_not_initialized(mock_get_ctx, client):
    """구독 요청 시 서비스가 초기화되지 않은 경우 503 에러 반환"""
    ctx = MagicMock()
    ctx.price_subscription_service = None
    mock_get_ctx.return_value = ctx

    response = client.post("/streaming/subscribe", json={"code": "005930", "reason": "ui_view"})
    
    assert response.status_code == 503
    assert response.json() == {"detail": "PriceSubscriptionService가 초기화되지 않았습니다"}

@patch("view.web.routes.streaming._get_ctx")
def test_subscribe_stock_success(mock_get_ctx, client, mock_ctx):
    """구독 요청 성공 테스트"""
    mock_get_ctx.return_value = mock_ctx
    
    response = client.post("/streaming/subscribe", json={"code": "005930", "reason": "ui_view"})
    
    assert response.status_code == 200
    
    # 1. 기대하는 JSON 응답에 'message' 필드 추가
    assert response.json() == {
        "success": True, 
        "code": "005930", 
        "category": "ui_ui_view",
        "message": "005930 종목이 실시간 가격 구독 대상에 추가되었습니다."
    }
    
    # 2. add_subscription 호출 인자에 StreamingType.UNIFIED_PRICE 추가
    mock_ctx.price_subscription_service.add_subscription.assert_awaited_once_with(
        "005930", 
        SubscriptionPriority.LOW, 
        "ui_ui_view", 
        StreamingType.UNIFIED_PRICE
    )

@patch("view.web.routes.streaming._get_ctx")
def test_unsubscribe_stock_service_not_initialized(mock_get_ctx, client):
    """구독 해지 시 서비스가 초기화되지 않은 경우 503 에러 반환"""
    ctx = MagicMock()
    ctx.price_subscription_service = None
    mock_get_ctx.return_value = ctx

    response = client.post("/streaming/unsubscribe", json={"code": "005930", "reason": "ui_view"})
    
    assert response.status_code == 503
    assert response.json() == {"detail": "PriceSubscriptionService가 초기화되지 않았습니다"}

@patch("view.web.routes.streaming._get_ctx")
def test_unsubscribe_stock_success(mock_get_ctx, client, mock_ctx):
    """구독 해지 성공 테스트"""
    mock_get_ctx.return_value = mock_ctx

    response = client.post("/streaming/unsubscribe", json={"code": "005930", "reason": "ui_view"})
    
    assert response.status_code == 200
    assert response.json() == {"success": True, "code": "005930", "category": "ui_ui_view"}
    
    mock_ctx.price_subscription_service.remove_subscription.assert_awaited_once_with(
        "005930", "ui_ui_view"
    )

@patch("view.web.routes.streaming._get_ctx")
def test_get_streaming_status_service_not_initialized(mock_get_ctx, client):
    """상태 조회 시 서비스가 초기화되지 않은 경우 기본값 반환"""
    ctx = MagicMock()
    ctx.price_subscription_service = None
    mock_get_ctx.return_value = ctx

    response = client.get("/streaming/status")
    
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"active_count": 0, "active_codes": []}}

@patch("view.web.routes.streaming._get_ctx")
def test_get_streaming_status_success(mock_get_ctx, client, mock_ctx):
    """상태 조회 성공 테스트"""
    mock_get_ctx.return_value = mock_ctx

    response = client.get("/streaming/status")
    
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"active_count": 2, "active_codes": ["005930", "000660"]}
    }
    mock_ctx.price_subscription_service.get_status.assert_called_once()


# ── GET /streaming/price/{code} SSE 엔드포인트 ───────────────────────────────

@patch("view.web.routes.streaming._get_ctx")
def test_stream_price_service_not_initialized(mock_get_ctx, client):
    """price_stream_service 미초기화 시 503 반환 검증"""
    ctx = MagicMock()
    ctx.price_stream_service = None
    mock_get_ctx.return_value = ctx

    response = client.get("/streaming/price/005930")
    assert response.status_code == 503
    assert response.json() == {"detail": "PriceStreamService가 초기화되지 않았습니다"}


async def test_stream_price_yields_tick_and_cleanup():
    """SSE 스트림: 틱 이벤트 전달 및 연결 종료 시 cleanup 검증"""
    tick_data = {"code": "005930", "price": 75000.0, "volume": 1500000}
    real_queue = asyncio.Queue()
    real_queue.put_nowait(tick_data)

    mock_stream_svc = MagicMock()
    mock_stream_svc.create_subscriber_queue.return_value = real_queue
    mock_sub_svc = AsyncMock()

    ctx = MagicMock()
    ctx.price_stream_service = mock_stream_svc
    ctx.price_subscription_service = mock_sub_svc

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=True)

    with patch("view.web.routes.streaming._get_ctx", return_value=ctx), \
         patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait_for:
        mock_wait_for.side_effect = [tick_data, asyncio.TimeoutError()]

        response = await stream_stock_price("005930", mock_request)

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

    assert any(f"data: {json.dumps(tick_data)}" in e for e in events)
    mock_stream_svc.create_subscriber_queue.assert_called_once_with("005930")
    mock_stream_svc.remove_subscriber_queue.assert_called_once_with("005930", real_queue)
    mock_sub_svc.remove_subscription.assert_awaited_once()


async def test_stream_price_subscribes_and_unsubscribes():
    """SSE 연결 시 add_subscription, 종료 시 remove_subscription 호출 검증"""
    real_queue = asyncio.Queue()
    mock_stream_svc = MagicMock()
    mock_stream_svc.create_subscriber_queue.return_value = real_queue
    mock_sub_svc = AsyncMock()

    ctx = MagicMock()
    ctx.price_stream_service = mock_stream_svc
    ctx.price_subscription_service = mock_sub_svc

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=True)

    with patch("view.web.routes.streaming._get_ctx", return_value=ctx), \
         patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait_for:
        mock_wait_for.side_effect = asyncio.TimeoutError()

        response = await stream_stock_price("005930", mock_request)
        async for _ in response.body_iterator:
            pass

    add_call = mock_sub_svc.add_subscription.call_args
    remove_call = mock_sub_svc.remove_subscription.call_args
    category = add_call.args[2]

    assert add_call.args[0] == "005930"
    assert category.startswith("sse_ui_")
    assert remove_call.args == ("005930", category)


async def test_stream_price_works_without_sub_svc():
    """price_subscription_service 없을 때도 SSE 스트림이 정상 동작하는지 검증"""
    real_queue = asyncio.Queue()
    mock_stream_svc = MagicMock()
    mock_stream_svc.create_subscriber_queue.return_value = real_queue

    ctx = MagicMock()
    ctx.price_stream_service = mock_stream_svc
    ctx.price_subscription_service = None

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=True)

    with patch("view.web.routes.streaming._get_ctx", return_value=ctx), \
         patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait_for:
        mock_wait_for.side_effect = asyncio.TimeoutError()

        response = await stream_stock_price("005930", mock_request)
        async for _ in response.body_iterator:
            pass

    mock_stream_svc.remove_subscriber_queue.assert_called_once_with("005930", real_queue)