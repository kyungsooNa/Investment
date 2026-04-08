"""
관심종목 API 엔드포인트 테스트 (favorite.html).
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from view.web import web_api


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_get_favorite_list_empty(web_client, mock_web_ctx):
    """GET /api/favorite - 빈 목록"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.get_with_details = AsyncMock(return_value=[])
        mock_web_ctx.price_subscription_service = None

        response = web_client.get("/api/favorite")
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.asyncio
async def test_add_favorite(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 추가 성공"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add.return_value = True

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["added"] is True
        assert data["code"] == "005930"
        mock_web_ctx.favorite_service.add.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_add_favorite_already_exists(web_client, mock_web_ctx):
    """POST /api/favorite/{code} - 이미 존재하는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.add.return_value = False

        response = web_client.post("/api/favorite/005930")
        assert response.status_code == 200
        assert response.json()["added"] is False


@pytest.mark.asyncio
async def test_remove_favorite(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 삭제 성공"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove.return_value = True

        response = web_client.delete("/api/favorite/005930")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["removed"] is True
        mock_web_ctx.favorite_service.remove.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_remove_favorite_not_found(web_client, mock_web_ctx):
    """DELETE /api/favorite/{code} - 없는 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.remove.return_value = False

        response = web_client.delete("/api/favorite/999999")
        assert response.status_code == 200
        assert response.json()["removed"] is False


@pytest.mark.asyncio
async def test_get_favorite_status_true(web_client, mock_web_ctx):
    """GET /api/favorite/{code}/status - 등록된 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.is_favorite.return_value = True

        response = web_client.get("/api/favorite/005930/status")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "005930"
        assert data["is_favorite"] is True


@pytest.mark.asyncio
async def test_get_favorite_status_false(web_client, mock_web_ctx):
    """GET /api/favorite/{code}/status - 미등록 종목"""
    with patch("view.web.routes.favorite._get_ctx", return_value=mock_web_ctx):
        mock_web_ctx.favorite_service = MagicMock()
        mock_web_ctx.favorite_service.is_favorite.return_value = False

        response = web_client.get("/api/favorite/000660/status")
        assert response.status_code == 200
        assert response.json()["is_favorite"] is False
