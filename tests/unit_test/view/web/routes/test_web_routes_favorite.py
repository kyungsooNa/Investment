"""
관심종목 API 엔드포인트 테스트 (favorite.html).
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from services.price_subscription_service import SubscriptionPriority
from view.web import web_api


async def test_get_favorite_list(web_client, mock_web_ctx):
    """GET /api/favorite - 목록 반환"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[
            {"code": "005930", "name": "삼성전자", "price": "75000", "rate": "1.5"},
        ])
        mock_web_ctx.price_subscription_service = None

        response = web_client.get("/api/favorite")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["code"] == "005930"
        assert data[0]["name"] == "삼성전자"


async def test_get_favorite_list_empty(web_client, mock_web_ctx):
    """GET /api/favorite - 빈 목록"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[])
        mock_web_ctx.price_subscription_service = None

        response = web_client.get("/api/favorite")
        assert response.status_code == 200
        assert response.json() == []


async def test_add_favorite(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 추가 성공"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = None
        mock_web_ctx.price_subscription_service = None

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["added"] is True
        assert data["code"] == "005930"
        mock_web_ctx.favorite_service.add.assert_called_once_with("005930", market="domestic")


async def test_add_favorite_already_exists(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 이미 존재하는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=False)
        mock_web_ctx.favorite_price_alert_service = None
        mock_web_ctx.price_subscription_service = None

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        assert response.json()["added"] is False


async def test_add_favorite_syncs_alert_cache_and_price_subscription(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 추가 시 가격 알림 캐시와 구독을 동기화한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.add_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.add_subscription = AsyncMock()

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200

        mock_web_ctx.favorite_price_alert_service.add_favorite.assert_awaited_once_with("005930")
        mock_web_ctx.price_subscription_service.add_subscription.assert_awaited_once()
        args = mock_web_ctx.price_subscription_service.add_subscription.await_args.args
        assert args[0] == "005930"
        assert args[1] == SubscriptionPriority.MEDIUM
        assert args[2] == "favorite"


async def test_add_favorite_unpadded_domestic_code_subscribes_padded_code(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 0 없는 국내 코드는 실시간 구독에 6자리로 전달한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.add_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.add_subscription = AsyncMock()

        response = web_client.post("/api/favorite/5930")
        assert response.status_code == 200
        assert response.json()["code"] == "005930"

        mock_web_ctx.favorite_service.add.assert_awaited_once_with("5930", market="domestic")
        mock_web_ctx.favorite_price_alert_service.add_favorite.assert_awaited_once_with("005930")
        args = mock_web_ctx.price_subscription_service.add_subscription.await_args.args
        assert args[0] == "005930"
        assert args[1] == SubscriptionPriority.MEDIUM
        assert args[2] == "favorite"


async def test_remove_favorite(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 삭제 성공"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = None
        mock_web_ctx.price_subscription_service = None

        response = web_client.delete("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["removed"] is True
        mock_web_ctx.favorite_service.remove.assert_called_once_with("005930", market="domestic")


async def test_remove_favorite_not_found(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 없는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=False)
        mock_web_ctx.favorite_price_alert_service = None
        mock_web_ctx.price_subscription_service = None

        response = web_client.delete("/api/favorite/999999")
        assert response.status_code == 200
        assert response.json()["removed"] is False


async def test_remove_favorite_syncs_alert_cache_and_price_subscription(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 삭제 시 가격 알림 캐시와 구독을 정리한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.remove_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.remove_subscription = AsyncMock()

        response = web_client.delete("/api/favorite/005930")
        assert response.status_code == 200

        mock_web_ctx.favorite_price_alert_service.remove_favorite.assert_awaited_once_with("005930")
        mock_web_ctx.price_subscription_service.remove_subscription.assert_awaited_once_with("005930", "favorite")


async def test_remove_favorite_unpadded_domestic_code_removes_padded_subscription(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 0 없는 국내 코드는 6자리 구독을 정리한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.remove_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.remove_subscription = AsyncMock()

        response = web_client.delete("/api/favorite/5930")
        assert response.status_code == 200
        assert response.json()["code"] == "005930"

        mock_web_ctx.favorite_price_alert_service.remove_favorite.assert_awaited_once_with("005930")
        assert mock_web_ctx.price_subscription_service.remove_subscription.await_args_list[0].args == (
            "005930",
            "favorite",
        )
        assert mock_web_ctx.price_subscription_service.remove_subscription.await_args_list[1].args == (
            "5930",
            "favorite",
        )


async def test_get_favorite_list_overseas_passes_market(web_client, mock_web_ctx):
    """GET /api/favorite?market=overseas_us - 미국장 목록을 조회한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[
            {"code": "AAPL", "name": "Apple Inc.", "exchange": "NASD"},
        ])
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.sync_subscriptions = AsyncMock()

        response = web_client.get("/api/favorite?market=overseas_us")
        assert response.status_code == 200
        assert response.json()[0]["code"] == "AAPL"
        mock_web_ctx.favorite_service.get_with_details.assert_awaited_once_with(
            market="overseas_us"
        )
        # 해외는 실시간 구독 대상이 아니다.
        mock_web_ctx.price_subscription_service.sync_subscriptions.assert_not_awaited()


async def test_add_favorite_overseas_syncs_overseas_alert_service_only(web_client, mock_web_ctx):
    """POST /api/favorite/{symbol}?market=overseas_us - 미국장 알림만 동기화하고 국내 구독은 건너뛴다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.add_favorite = AsyncMock()
        mock_web_ctx.overseas_favorite_price_alert_service = MagicMock()
        mock_web_ctx.overseas_favorite_price_alert_service.add_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.add_subscription = AsyncMock()

        response = web_client.post("/api/favorite/AAPL?market=overseas_us")
        assert response.status_code == 200
        data = response.json()
        assert data["added"] is True
        assert data["market"] == "overseas_us"
        mock_web_ctx.favorite_service.add.assert_called_once_with("AAPL", market="overseas_us")
        mock_web_ctx.overseas_favorite_price_alert_service.add_favorite.assert_awaited_once_with("AAPL")
        mock_web_ctx.favorite_price_alert_service.add_favorite.assert_not_awaited()
        mock_web_ctx.price_subscription_service.add_subscription.assert_not_awaited()


async def test_remove_favorite_overseas_syncs_overseas_alert_service_only(web_client, mock_web_ctx):
    """DELETE /api/favorite/{symbol}?market=overseas_us - 미국장 알림 상태만 정리한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=True)
        mock_web_ctx.favorite_price_alert_service = MagicMock()
        mock_web_ctx.favorite_price_alert_service.remove_favorite = AsyncMock()
        mock_web_ctx.overseas_favorite_price_alert_service = MagicMock()
        mock_web_ctx.overseas_favorite_price_alert_service.remove_favorite = AsyncMock()
        mock_web_ctx.price_subscription_service = MagicMock()
        mock_web_ctx.price_subscription_service.remove_subscription = AsyncMock()

        response = web_client.delete("/api/favorite/AAPL?market=overseas_us")
        assert response.status_code == 200
        mock_web_ctx.favorite_service.remove.assert_called_once_with("AAPL", market="overseas_us")
        mock_web_ctx.overseas_favorite_price_alert_service.remove_favorite.assert_awaited_once_with("AAPL")
        mock_web_ctx.favorite_price_alert_service.remove_favorite.assert_not_awaited()
        mock_web_ctx.price_subscription_service.remove_subscription.assert_not_awaited()


async def test_favorite_rejects_unknown_market(web_client, mock_web_ctx):
    """알 수 없는 market 값은 400으로 거절한다."""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[])

        response = web_client.get("/api/favorite?market=crypto")
        assert response.status_code == 400
        mock_web_ctx.favorite_service.get_with_details.assert_not_awaited()


async def test_get_favorite_status_true(web_client, mock_web_ctx):
    """GET /api/favorite/{code}/status - 등록된 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.is_favorite = AsyncMock(return_value=True)

        response = web_client.get("/api/favorite/005930/status")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "005930"
        assert data["is_favorite"] is True


async def test_get_favorite_status_false(web_client, mock_web_ctx):
    """GET /api/favorite/{code}/status - 미등록 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.is_favorite = AsyncMock(return_value=False)

        response = web_client.get("/api/favorite/000660/status")
        assert response.status_code == 200
        assert response.json()["is_favorite"] is False


async def test_get_favorite_list_with_subscription(web_client, mock_web_ctx):
    """GET /api/favorite - SSE 구독 등록 로직 정상 실행"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[
            {"code": "005930", "name": "삼성전자", "price": "75000", "rate": "1.5"},
        ])
        
        mock_sub_svc = AsyncMock()
        mock_web_ctx.price_subscription_service = mock_sub_svc
        
        response = web_client.get("/api/favorite")
        assert response.status_code == 200
        
        # 백그라운드 태스크(SSE 구독) 실행 대기
        await asyncio.sleep(0)
        
        mock_sub_svc.sync_subscriptions.assert_awaited_once()
        _, kwargs = mock_sub_svc.sync_subscriptions.call_args
        assert kwargs["codes"] == ["005930"]
        assert kwargs["category_key"] == "favorite"
        assert kwargs["priority"] == SubscriptionPriority.MEDIUM


async def test_get_favorite_list_subscription_exception(web_client, mock_web_ctx):
    """GET /api/favorite - SSE 구독 등록 중 예외 발생 시 로깅 확인"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[
            {"code": "005930", "name": "삼성전자", "price": "75000", "rate": "1.5"},
        ])
        
        mock_sub_svc = AsyncMock()
        mock_sub_svc.sync_subscriptions.side_effect = Exception("Subscription Error")
        mock_web_ctx.price_subscription_service = mock_sub_svc
        mock_web_ctx.logger = MagicMock()
        
        response = web_client.get("/api/favorite")
        assert response.status_code == 200
        
        # 백그라운드 태스크(SSE 구독) 실행 대기
        await asyncio.sleep(0)
        
        mock_web_ctx.logger.warning.assert_called_once()
        assert "Subscription Error" in mock_web_ctx.logger.warning.call_args[0][0]


async def test_favorite_api_allows_unauthenticated_when_login_disabled(test_app, mock_web_ctx):
    """use_login=false(로컬 실행)면 세션 쿠키 없이도 관심종목 API를 사용할 수 있다."""
    from fastapi.testclient import TestClient

    mock_web_ctx.full_config["use_login"] = False
    mock_web_ctx.favorite_service = MagicMock()
    mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[])
    mock_web_ctx.favorite_service.add = AsyncMock(return_value=True)
    mock_web_ctx.price_subscription_service = None

    client = TestClient(test_app)  # 세션 쿠키·CSRF 토큰 없음

    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        assert client.get("/api/favorite").status_code == 200
        # CSRF 토큰이 없어도 상태 변경 요청이 통과해야 한다
        assert client.post("/api/favorite/005930").status_code == 200
