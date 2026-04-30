# tests/integration_test/test_it_web_app_e2e_smoke.py
"""
실제 view.web.web_main.app 조립을 검증하는 E2E smoke 테스트.

기존 Web API 통합 테스트가 라우터만 붙인 테스트 앱을 쓰는 것과 달리,
이 파일은 web_main.app의 lifespan, middleware, page router, static mount를 통과한다.
외부 I/O와 장시간 백그라운드 작업은 WebAppContext fake로 차단한다.
"""
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from common.types import ResCommonResponse
import view.web.api_common as api_common
from view.web.routes import stock as stock_routes
import view.web.web_main as web_main


class _ForegroundContext(AbstractAsyncContextManager):
    def __init__(self, scheduler):
        self.scheduler = scheduler

    async def __aenter__(self):
        self.scheduler.enter_count += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.scheduler.exit_count += 1
        return False


class _ForegroundScheduler:
    def __init__(self):
        self.enter_count = 0
        self.exit_count = 0
        self.context = MagicMock(side_effect=lambda: _ForegroundContext(self))


@pytest.fixture(autouse=True)
def clear_web_state():
    api_common.set_ctx(None)
    stock_routes._status_cache = None
    stock_routes._status_cache_ts = 0.0
    api_common._active_requests.clear()
    api_common._recent_completed.clear()
    yield
    api_common.set_ctx(None)
    stock_routes._status_cache = None
    stock_routes._status_cache_ts = 0.0
    api_common._active_requests.clear()
    api_common._recent_completed.clear()


@pytest.fixture
def fake_web_ctx():
    ctx = MagicMock()
    ctx.full_config = {"use_login": False, "auth": {"secret_key": "test-token"}}
    ctx.initialized = True
    ctx.env = SimpleNamespace(
        is_paper_trading=True,
        active_config={
            "stock_account_number": "12345678-01",
            "auth": {"secret_key": "test-token"},
        },
    )

    ctx.load_config_and_env = MagicMock()
    ctx.initialize_services = AsyncMock(return_value=True)
    ctx.initialize_scheduler = MagicMock()
    ctx.start_background_tasks = MagicMock()
    ctx.shutdown = AsyncMock()

    ctx.scheduler = MagicMock()
    ctx.scheduler.restore_state = AsyncMock()
    ctx.scheduler.stop = AsyncMock()
    ctx.scheduler._running = True

    ctx.order_execution_service = MagicMock()
    ctx.order_execution_service.restore_state_from_broker = AsyncMock()
    ctx.order_execution_service.reconcile_orders_with_broker = AsyncMock()
    ctx.order_execution_service.handle_buy_stock = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd="0",
            msg1="주문 성공",
            data={"ord_no": "0000123456"},
        )
    )
    ctx.order_execution_service.handle_sell_stock = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd="0",
            msg1="주문 성공",
            data={"ord_no": "0000654321"},
        )
    )

    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = "timer-token"
    ctx.pm.log_timer = MagicMock()

    ctx.is_market_open_now = AsyncMock(return_value=True)
    ctx.get_env_type.return_value = "모의투자"
    ctx.get_current_time_str.return_value = "2026-03-08 10:30:00"
    ctx.foreground_scheduler = _ForegroundScheduler()
    ctx.price_subscription_service = None

    return ctx


@pytest.fixture
def client_with_fake_lifespan(fake_web_ctx, mocker):
    mocker.patch.object(web_main, "WebAppContext", return_value=fake_web_ctx)
    with TestClient(web_main.app) as client:
        yield client


def test_web_main_lifespan_initializes_and_shutdowns_context(fake_web_ctx, mocker):
    mocker.patch.object(web_main, "WebAppContext", return_value=fake_web_ctx)

    with TestClient(web_main.app):
        fake_web_ctx.load_config_and_env.assert_called_once_with()
        fake_web_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=True)
        fake_web_ctx.initialize_scheduler.assert_called_once_with()
        fake_web_ctx.scheduler.restore_state.assert_awaited_once_with()
        fake_web_ctx.order_execution_service.restore_state_from_broker.assert_awaited_once_with()
        fake_web_ctx.order_execution_service.reconcile_orders_with_broker.assert_awaited_once_with()
        fake_web_ctx.start_background_tasks.assert_called_once_with()
        assert api_common._ctx is fake_web_ctx

    fake_web_ctx.shutdown.assert_awaited_once_with()
    fake_web_ctx.scheduler.stop.assert_awaited_once_with(save_state=True)


def test_real_app_serves_core_pages_without_login(client_with_fake_lifespan):
    cases = {
        "/": "Investment",
        "/stock": 'id="stock-code-input"',
        "/order": 'id="order-code"',
        "/virtual": 'id="virtual-summary-box"',
    }

    for path, expected in cases.items():
        response = client_with_fake_lifespan.get(path)
        assert response.status_code == 200
        assert expected in response.text


def test_real_app_serves_static_assets(client_with_fake_lifespan):
    cases = {
        "/static/js/order.js": "placeOrder",
        "/static/js/stock.js": "searchStock",
        "/static/css/style.css": "body",
    }

    for path, expected in cases.items():
        response = client_with_fake_lifespan.get(path)
        assert response.status_code == 200
        assert expected in response.text


def test_real_app_status_api_uses_initialized_context(client_with_fake_lifespan, fake_web_ctx):
    response = client_with_fake_lifespan.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "market_open": True,
        "env_type": "모의투자",
        "is_paper_trading": True,
        "current_time": "2026-03-08 10:30:00",
        "initialized": True,
    }
    fake_web_ctx.is_market_open_now.assert_awaited_once_with()


def test_real_app_order_api_passes_through_middleware_and_service(
    client_with_fake_lifespan,
    fake_web_ctx,
):
    response = client_with_fake_lifespan.post(
        "/api/order",
        json={"code": "005930", "qty": "2", "price": "70000", "side": "buy"},
    )

    assert response.status_code == 200
    assert response.json()["rt_cd"] == "0"
    assert response.json()["data"]["ord_no"] == "0000123456"
    fake_web_ctx.order_execution_service.handle_buy_stock.assert_awaited_once_with(
        "005930",
        "2",
        "70000",
        source="manual:수동매매",
        finalize_immediately=False,
    )
    fake_web_ctx.pm.log_timer.assert_called_once_with("place_order", "timer-token")
    assert fake_web_ctx.foreground_scheduler.enter_count == 1
    assert fake_web_ctx.foreground_scheduler.exit_count == 1
    fake_web_ctx.foreground_scheduler.context.assert_called_once_with()
    assert not api_common._active_requests
    assert any(item["path"] == "/api/order" for item in api_common._recent_completed)


def test_real_app_page_falls_back_to_login_when_context_missing():
    api_common.set_ctx(None)

    response = TestClient(web_main.app).get("/")

    assert response.status_code == 200
    assert "Investment Login" in response.text
    assert 'id="username"' in response.text
