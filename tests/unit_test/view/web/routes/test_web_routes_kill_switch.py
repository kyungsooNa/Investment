"""Kill Switch 라우트 단위 테스트 (reset-strategy 경로 중심)."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_reset_strategy_kill_switch_success(web_client, mock_web_ctx):
    """단일 전략 Kill Switch 해제 — 200 + reset_strategy 위임."""
    mock_web_ctx.kill_switch_service.reset_strategy = AsyncMock()
    web_client.cookies.set("access_token", "secret")

    resp = web_client.post("/api/kill-switch/reset-strategy/momentum")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "momentum" in body["message"]
    mock_web_ctx.kill_switch_service.reset_strategy.assert_awaited_once()
    await_args = mock_web_ctx.kill_switch_service.reset_strategy.await_args
    assert await_args.args[0] == "momentum"
    assert await_args.args[1] == "secret"  # operator = access_token 쿠키


@pytest.mark.asyncio
async def test_reset_strategy_kill_switch_service_unavailable(web_client, mock_web_ctx):
    """Kill Switch 서비스 미초기화 시 503."""
    mock_web_ctx.kill_switch_service = None
    web_client.cookies.set("access_token", "secret")

    resp = web_client.post("/api/kill-switch/reset-strategy/momentum")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_reset_strategy_kill_switch_requires_auth(web_client, mock_web_ctx):
    """인증 쿠키가 없으면 401."""
    web_client.cookies.clear()

    resp = web_client.post("/api/kill-switch/reset-strategy/momentum")

    assert resp.status_code == 401
