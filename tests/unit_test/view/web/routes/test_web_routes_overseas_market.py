"""
미국장 시가총액/랭킹 API 엔드포인트 테스트.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from common.types import ResCommonResponse


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


async def test_top_market_cap_disables_http_cache(web_client, mock_web_ctx):
    """자동 갱신 화면이 같은 URL을 호출해도 HTTP 캐시가 이전 스냅샷을 돌려주면 안 된다."""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_top_market_cap = AsyncMock(
            return_value={"fx_rate": None, "items": [], "updated_at": 1787148000}
        )

        response = web_client.get("/api/overseas/top-market-cap?limit=10")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"


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


def _ranking_payload():
    return {
        "fx_rate": 1400.0,
        "items": [{
            "rank": 1, "symbol": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
            "price": 200.0, "change_rate": 3.0, "volume": 1000,
            "trading_value_usd": 200_000, "market_cap_usd": 3_000_000_000_000,
            "market_cap_krw": 4_200_000_000_000_000,
        }],
    }


async def test_ranking_returns_items(web_client, mock_web_ctx):
    """GET /api/overseas/ranking/{category} - 정상 응답"""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_ranking = AsyncMock(
            return_value=_ranking_payload()
        )

        response = web_client.get("/api/overseas/ranking/rise?limit=50")

        assert response.status_code == 200
        body = response.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["items"][0]["symbol"] == "AAPL"
        mock_web_ctx.overseas_market_stats_service.get_ranking.assert_awaited_once_with(
            "rise", limit=50
        )


async def test_ranking_accepts_every_supported_category(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_ranking = AsyncMock(
            return_value=_ranking_payload()
        )

        for category in ("rise", "fall", "volume", "trading_value"):
            assert web_client.get(f"/api/overseas/ranking/{category}").status_code == 200


async def test_ranking_rejects_unknown_category(web_client, mock_web_ctx):
    """국내 전용 수급 랭킹 등 미지원 구분은 400."""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_ranking = AsyncMock()

        response = web_client.get("/api/overseas/ranking/foreign_buy")

        assert response.status_code == 400
        mock_web_ctx.overseas_market_stats_service.get_ranking.assert_not_awaited()


async def test_ranking_blocked_when_overseas_disabled(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.enabled_market_modes = ["domestic"]
        mock_web_ctx.overseas_market_stats_service = MagicMock()

        assert web_client.get("/api/overseas/ranking/rise").status_code == 400


async def test_ranking_returns_503_when_service_missing(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = None

        assert web_client.get("/api/overseas/ranking/rise").status_code == 503


async def test_ranking_rejects_out_of_range_limit(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()

        assert web_client.get("/api/overseas/ranking/rise?limit=0").status_code == 422
        assert web_client.get("/api/overseas/ranking/rise?limit=501").status_code == 422


async def test_ranking_failure_returns_error_payload(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_ranking = AsyncMock(
            side_effect=RuntimeError("yahoo down")
        )

        response = web_client.get("/api/overseas/ranking/rise")

        assert response.status_code == 200
        assert response.json()["rt_cd"] == "1"


async def test_ranking_timeout_returns_error_payload(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_market_stats_service = MagicMock()
        mock_web_ctx.overseas_market_stats_service.get_ranking = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        response = web_client.get("/api/overseas/ranking/rise")

        assert response.status_code == 200
        assert response.json()["rt_cd"] == "1"


async def test_trades_returns_summary_and_rows(web_client, mock_web_ctx):
    """GET /api/overseas/trades — USD 원장 성과 요약 + 거래 목록"""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        ledger = MagicMock()
        ledger.get_summary.return_value = {
            "total_trades": 2, "sold_trades": 1, "win_rate": 100.0, "avg_return": 9.48,
        }
        ledger.get_all_trades.return_value = [
            {"symbol": "AAPL", "status": "SOLD", "return_rate": 9.48},
        ]
        mock_web_ctx.overseas_trade_repository = ledger

        response = web_client.get("/api/overseas/trades")

        assert response.status_code == 200
        body = response.json()
        assert body["rt_cd"] == "0"
        assert body["data"]["summary"]["win_rate"] == 100.0
        assert body["data"]["trades"][0]["symbol"] == "AAPL"


async def test_trades_requires_overseas_enabled(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.enabled_market_modes = ["domestic"]
        mock_web_ctx.overseas_trade_repository = MagicMock()

        response = web_client.get("/api/overseas/trades")

        assert response.status_code == 400


async def test_trades_without_ledger_returns_503(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_trade_repository = None

        response = web_client.get("/api/overseas/trades")

        assert response.status_code == 503


def _install_fill_reconcile(ctx, data=None):
    from unittest.mock import AsyncMock

    service = MagicMock()
    service.reconcile = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="해외 체결 대사 완료",
        data=data or {
            "applied": False, "checked": 1,
            "counts": {"ok": 1, "partial": 0, "unfilled": 0, "unknown": 0},
            "diffs": [],
        },
    ))
    ctx.overseas_fill_reconcile_service = service
    return service


async def test_reconcile_defaults_to_dry_run(web_client, mock_web_ctx):
    """apply 를 명시하지 않으면 원장을 건드리지 않는 판정만 돈다."""
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        service = _install_fill_reconcile(mock_web_ctx)

        response = web_client.post(
            "/api/overseas/trades/reconcile?start_date=20260813&end_date=20260813"
        )

        assert response.status_code == 200
        assert response.json()["rt_cd"] == "0"
        assert service.reconcile.await_args.kwargs["apply"] is False


async def test_reconcile_passes_apply_flag(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        service = _install_fill_reconcile(mock_web_ctx)

        web_client.post(
            "/api/overseas/trades/reconcile"
            "?start_date=20260813&end_date=20260813&exchange=NYSE&apply=true"
        )

        kwargs = service.reconcile.await_args.kwargs
        assert kwargs["apply"] is True
        assert kwargs["exchange"] == "NYSE"


async def test_reconcile_requires_overseas_enabled(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.enabled_market_modes = ["domestic"]
        _install_fill_reconcile(mock_web_ctx)

        response = web_client.post(
            "/api/overseas/trades/reconcile?start_date=20260813&end_date=20260813"
        )

        assert response.status_code == 400


async def test_reconcile_without_service_returns_503(web_client, mock_web_ctx):
    with patch("view.web.routes.overseas_market._get_ctx", return_value=mock_web_ctx):
        _enable_overseas(mock_web_ctx)
        mock_web_ctx.overseas_fill_reconcile_service = None

        response = web_client.post(
            "/api/overseas/trades/reconcile?start_date=20260813&end_date=20260813"
        )

        assert response.status_code == 503
