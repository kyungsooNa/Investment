"""
OHLCV 수집 제어 관련 테스트 (ohlcv.py).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_get_ohlcv_progress_success(web_client, mock_web_ctx):
    """GET /api/ohlcv/progress 정상 진행률 반환 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True, "processed": 100, "total": 1000}
    mock_web_ctx.ohlcv_update_task = mock_task

    response = web_client.get("/api/ohlcv/progress")
    
    assert response.status_code == 200
    assert response.json()["running"] is True
    assert response.json()["processed"] == 100
    assert response.json()["total"] == 1000


@pytest.mark.asyncio
async def test_get_ohlcv_progress_not_initialized(web_client, mock_web_ctx):
    """GET /api/ohlcv/progress 태스크 미초기화 시 503 반환 테스트"""
    mock_web_ctx.ohlcv_update_task = None

    response = web_client.get("/api/ohlcv/progress")
    assert response.status_code == 503
    assert "초기화되지 않았습니다" in response.json()["detail"]


@pytest.mark.asyncio
async def test_force_ohlcv_update_success(web_client, mock_web_ctx):
    """POST /api/ohlcv/force-update 정상 강제 수집 트리거 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_collect = AsyncMock()
    mock_web_ctx.ohlcv_update_task = mock_task

    response = web_client.post("/api/ohlcv/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "시작되었습니다" in response.json()["message"]
    
    # 백그라운드 Task가 이벤트 루프에서 실행될 수 있도록 제어권 양보
    await asyncio.sleep(0)
    mock_task.force_collect.assert_called_once()


@pytest.mark.asyncio
async def test_force_ohlcv_update_already_running(web_client, mock_web_ctx):
    """POST /api/ohlcv/force-update 이미 수집 중일 때 409 반환 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.ohlcv_update_task = mock_task

    response = web_client.post("/api/ohlcv/force-update")
    
    assert response.status_code == 409
    assert "진행 중입니다" in response.json()["detail"]


@pytest.mark.asyncio
async def test_force_ohlcv_update_not_initialized(web_client, mock_web_ctx):
    """POST /api/ohlcv/force-update 태스크 미초기화 시 503 반환 테스트"""
    mock_web_ctx.ohlcv_update_task = None

    response = web_client.post("/api/ohlcv/force-update")
    
    assert response.status_code == 503
    assert "초기화되지 않았습니다" in response.json()["detail"]