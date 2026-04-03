import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch

from view.web.routes.streaming import router
from services.price_subscription_service import SubscriptionPriority

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
    assert response.json() == {"success": True, "code": "005930", "category": "ui_ui_view"}
    
    mock_ctx.price_subscription_service.add_subscription.assert_awaited_once_with(
        "005930", SubscriptionPriority.LOW, "ui_ui_view"
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