"""
미국장 시가총액/랭킹 API 엔드포인트 테스트.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _enable_overseas(ctx):
    ctx.enabled_market_modes = ["domestic", "overseas_us"]


async def test_top_market_cap_returns_items(web_client, mock_web_ctx):
    """GET /api/overseas/top-market-cap - 정상 응답"""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap = AsyncMock(return_value={
            "fx_rate": 1400.0,
            "items": [{
                "rank": 1, "symbol": "MSFT", "name": "Microsoft", "sector": "Information Technology",
                "price": 410.0, "change_rate": 0.4, "volume": 1000,
                "market_cap_usd": 4_000_000_000_000, "market_cap_krw": 5_600_000_000_000_000,
            }],
        })

        response = web_client.get("/api/overseas/top-market-cap?limit=10")

        assert response.status_code == 200
        body = response.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["fx_rate"] == 1400.0
        assert body["data"]["items"][0]["symbol"] == "MSFT"
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap.assert_awaited_once_with(limit=10)


async def test_top_market_cap_defaults_limit_to_30(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap = AsyncMock(
            return_value={"fx_rate": None, "items": []}
        )

        assert web_client.get("/api/overseas/top-market-cap").status_code == 200
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap.assert_awaited_once_with(limit=30)


async def test_top_market_cap_rejects_out_of_range_limit(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()

        assert web_client.get("/api/overseas/top-market-cap?limit=0").status_code == 422
        assert web_client.get("/api/overseas/top-market-cap?limit=501").status_code == 422


async def test_top_market_cap_blocked_when_overseas_disabled(web_client, mock_web_ctx):
    """overseas_us 가 enabled 되지 않은 run 에서는 400."""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.enabled_market_modes = ["domestic"]
        mock_web_ctx.overseas_market_stats_service = MagicMock()

        response = web_client.get("/api/overseas/top-market-cap")

        assert response.status_code == 400


async def test_top_market_cap_returns_503_when_service_missing(web_client, mock_web_ctx):
    """유니버스 DB 준비 실패 등으로 서비스가 조립되지 않았으면 503."""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = None

        response = web_client.get("/api/overseas/top-market-cap")

        assert response.status_code == 503


async def test_top_market_cap_timeout_returns_error_payload(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        response = web_client.get("/api/overseas/top-market-cap")

        assert response.status_code == 200
        assert response.json()["rt_cd"] == "1"


async def test_top_market_cap_provider_failure_returns_error_payload(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap = AsyncMock(
            side_effect=RuntimeError("yahoo down")
        )

        response = web_client.get("/api/overseas/top-market-cap")

        assert response.status_code == 200
        body = response.json()
        assert body["rt_cd"] == "1"
        assert body["data"] is None
