"""
관심종목 API 엔드포인트 테스트 (favorite.html).
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
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

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["added"] is True
        assert data["code"] == "005930"
        mock_web_ctx.favorite_service.add.assert_called_once_with("005930")


async def test_add_favorite_already_exists(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 이미 존재하는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add = AsyncMock(return_value=False)

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        assert response.json()["added"] is False


async def test_remove_favorite(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 삭제 성공"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=True)

        response = web_client.delete("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["removed"] is True
        mock_web_ctx.favorite_service.remove.assert_called_once_with("005930")


async def test_remove_favorite_not_found(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 없는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove = AsyncMock(return_value=False)

        response = web_client.delete("/api/favorite/999999")
        assert response.status_code == 200
        assert response.json()["removed"] is False


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
