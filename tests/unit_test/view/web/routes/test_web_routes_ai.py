"""GET /api/ai/usage 엔드포인트 테스트."""
from unittest.mock import AsyncMock, MagicMock


_SNAPSHOT = {
    "enabled": True,
    "period_key": "2026-07-20",
    "used": 12,
    "interactive_used": 9,
    "disclosure_used": 3,
    "daily_limit": 100,
    "interactive_limit": 80,
    "disclosure_reserve": 20,
    "remaining": 88,
    "reset_at": "2026-07-21T00:00:00-07:00",
    "by_type": {"stock": 5, "news": 4, "disclosure": 3},
}


def test_ai_usage_returns_snapshot_with_per_type_breakdown(web_client, mock_web_ctx):
    mock_web_ctx.ai_usage_limiter = MagicMock()
    mock_web_ctx.ai_usage_limiter.get_snapshot = AsyncMock(return_value=_SNAPSHOT)

    response = web_client.get("/api/ai/usage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rt_cd"] == "0"
    assert payload["data"]["remaining"] == 88
    assert payload["data"]["by_type"]["news"] == 4


def test_ai_usage_reports_disabled_when_limiter_missing(web_client, mock_web_ctx):
    mock_web_ctx.ai_usage_limiter = None

    response = web_client.get("/api/ai/usage")

    assert response.status_code == 200
    assert response.json()["data"]["enabled"] is False


def test_ai_usage_returns_error_code_on_snapshot_failure(web_client, mock_web_ctx):
    mock_web_ctx.ai_usage_limiter = MagicMock()
    mock_web_ctx.ai_usage_limiter.get_snapshot = AsyncMock(
        side_effect=RuntimeError("db locked")
    )

    response = web_client.get("/api/ai/usage")

    assert response.status_code == 200
    assert response.json()["rt_cd"] == "1"
