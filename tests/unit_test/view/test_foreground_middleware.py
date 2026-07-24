"""
Foreground 우선순위 미들웨어 단위 테스트.
Broker API 호출 라우트에만 foreground context가 적용되는지 검증.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport

# 미들웨어에서 사용하는 경로 판별 함수를 직접 테스트
from view.web.web_main import _needs_foreground


# --- _needs_foreground 경로 판별 테스트 ---


class TestNeedsForeground:
    """_needs_foreground() 경로 판별 함수 검증."""

    @pytest.mark.parametrize("path", [
        "/api/stock/005930",
        "/api/chart/005930",
        "/api/indicator/bollinger/005930",
        "/api/indicator/rsi/005930",
        "/api/indicator/ma/005930",
        "/api/balance",
        "/api/order",
        "/api/ranking/rise",
        "/api/ranking/volume",
        "/api/top-market-cap",
        "/api/program-trading/subscribe",
        "/api/program-trading/history/005930",
        "/api/program-trading/unsubscribe",
        "/api/virtual/chart/ALL",
        "/api/virtual/history",
    ])
    def test_included_paths(self, path):
        """Broker API를 호출하는 경로는 foreground 대상."""
        assert _needs_foreground(path) is True

    @pytest.mark.parametrize("path", [
        "/api/status",
        "/api/stock/search",
        "/api/ranking/progress",
        "/api/program-trading/status",
        "/api/program-trading/stream",
        "/api/program-trading/save-data",
        "/api/program-trading/load-data",
        "/api/program-trading/db-status",
        "/api/scheduler/status",
        "/api/scheduler/start",
        "/api/scheduler/stop",
        "/api/scheduler/history",
        "/api/scheduler/stream",
        "/api/scheduler/strategy/RSI2눌림목/start",
        "/api/scheduler/strategy/RSI2눌림목/stop",
        "/api/scheduler/strategy/RSI2눌림목/max-positions",
        "/api/virtual/summary",
        "/api/virtual/strategies",
        "/api/notifications/recent",
        "/api/notifications/stream",
        "/api/auth/login",
        "/api/environment",
        "/",
        "/stock",
        "/balance",
        "/static/css/style.css",
    ])
    def test_excluded_paths(self, path):
        """로컬 전용, SSE 스트리밍, 페이지 경로는 foreground 비대상."""
        assert _needs_foreground(path) is False


# --- 미들웨어 통합 테스트 (FastAPI TestClient) ---


class _FakeContextManager:
    """fg.context() 반환용 async context manager mock."""
    async def __aenter__(self):
        return None
    async def __aexit__(self, *args):
        return False


def _make_mock_ctx(with_fg=True):
    """ForegroundScheduler가 포함된 mock WebAppContext 생성."""
    ctx = MagicMock()
    ctx.full_config = {"auth": {"secret_key": "test-token"}}

    if with_fg:
        ctx.foreground_scheduler = MagicMock()
        ctx.foreground_scheduler.context = MagicMock(return_value=_FakeContextManager())
    else:
        ctx.foreground_scheduler = None

    # balance 라우트가 호출하는 서비스들을 AsyncMock으로 설정
    mock_resp = MagicMock()
    mock_resp.rt_cd = "0"
    mock_resp.msg1 = "ok"
    mock_resp.data = {}
    mock_resp.to_dict.return_value = {"rt_cd": "0", "msg1": "ok", "data": {}}
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.handle_get_account_balance = AsyncMock(return_value=mock_resp)

    # ranking/progress 라우트가 호출하는 멤버
    ctx.ranking_task = MagicMock()
    ctx.ranking_task.get_investor_ranking_progress.return_value = {
        "running": False, "processed": 0, "total": 0, "collected": 0, "elapsed": 0.0
    }

    ctx.pm = MagicMock()
    ctx.pm.start_timer.return_value = 0
    ctx.env = None
    ctx.broker = None

    return ctx


@pytest.mark.asyncio
async def test_middleware_calls_foreground_on_api_route():
    """Broker API 경로 요청 시 fg.context()가 호출된다."""
    mock_ctx = _make_mock_ctx(with_fg=True)

    with patch("view.web.api_common._ctx", mock_ctx):
        from view.web.web_main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"access_token": "test-token"},
        ) as client:
            resp = await client.get("/api/balance")

    assert resp.status_code == 200
    mock_ctx.foreground_scheduler.context.assert_called()


@pytest.mark.asyncio
async def test_middleware_skips_foreground_on_excluded_route():
    """제외 경로 요청 시 fg.context()가 호출되지 않는다."""
    mock_ctx = _make_mock_ctx(with_fg=True)

    with patch("view.web.api_common._ctx", mock_ctx):
        from view.web.web_main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"access_token": "test-token"},
        ) as client:
            resp = await client.get("/api/ranking/progress")

    assert resp.status_code == 200
    mock_ctx.foreground_scheduler.context.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_graceful_without_foreground_scheduler():
    """foreground_scheduler가 None일 때도 정상 동작한다."""
    mock_ctx = _make_mock_ctx(with_fg=False)

    with patch("view.web.api_common._ctx", mock_ctx):
        from view.web.web_main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"access_token": "test-token"},
        ) as client:
            resp = await client.get("/api/balance")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_middleware_graceful_without_ctx():
    """ctx가 None일 때도 정상 동작한다."""
    with patch("view.web.api_common._ctx", None):
        from view.web.web_main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/balance")
            assert resp.status_code == 503
