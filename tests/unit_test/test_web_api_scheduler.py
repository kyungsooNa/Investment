"""
스케줄러 관련 테스트 (scheduler.html).
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_scheduler_endpoints(web_client, mock_web_ctx):
    """스케줄러 관련 엔드포인트 테스트"""
    # Status
    mock_web_ctx.scheduler.get_status = MagicMock(return_value={"running": False})
    response = web_client.get("/api/scheduler/status")
    assert response.status_code == 200
    assert response.json()["running"] is False

    # Start
    response = web_client.post("/api/scheduler/start")
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_web_ctx.scheduler.start.assert_awaited_once()

    # Stop
    response = web_client.post("/api/scheduler/stop")
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_web_ctx.scheduler.stop.assert_awaited_once()

    # History
    mock_web_ctx.scheduler.get_signal_history = MagicMock(return_value=[])
    response = web_client.get("/api/scheduler/history")
    assert response.status_code == 200
    assert response.json()["history"] == []


@pytest.mark.asyncio
async def test_get_scheduler_history_name_correction(web_client, mock_web_ctx):
    """
    GET /api/scheduler/history 엔드포인트가 잘못된 종목명을 올바르게 보정하는지 테스트.
    """
    incorrect_history = [
        {
            "code": "005930",
            "name": "반도체",
            "action": "BUY",
            "price": 70000,
            "reason": "Test Signal",
            "strategy_name": "TestStrategy",
            "timestamp": "2023-01-01 10:00:00",
            "api_success": True
        }
    ]
    mock_web_ctx.scheduler.get_signal_history.return_value = incorrect_history

    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code.return_value = "삼성전자"
    mock_web_ctx.stock_code_mapper = mock_mapper

    response = web_client.get("/api/scheduler/history")

    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 1
    assert data["history"][0]["name"] == "삼성전자"
    mock_mapper.get_name_by_code.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_scheduler_strategy_control(web_client, mock_web_ctx):
    """스케줄러 개별 전략 제어 테스트"""
    # Start Strategy
    mock_web_ctx.scheduler.start_strategy = AsyncMock(return_value=True)
    mock_web_ctx.scheduler.get_status = MagicMock(return_value={})

    resp = web_client.post("/api/scheduler/strategy/TestStrat/start")
    assert resp.status_code == 200
    mock_web_ctx.scheduler.start_strategy.assert_awaited_with("TestStrat")

    # Stop Strategy
    mock_web_ctx.scheduler.stop_strategy = MagicMock(return_value=True)
    resp = web_client.post("/api/scheduler/strategy/TestStrat/stop")
    assert resp.status_code == 200
    mock_web_ctx.scheduler.stop_strategy.assert_called_with("TestStrat")


@pytest.mark.asyncio
async def test_scheduler_not_initialized(web_client, mock_web_ctx):
    """스케줄러 미초기화 상태 테스트"""
    mock_web_ctx.scheduler = None

    assert web_client.get("/api/scheduler/status").json()["running"] is False
    assert web_client.post("/api/scheduler/start").status_code == 503
    assert web_client.post("/api/scheduler/stop").status_code == 503
    assert web_client.post("/api/scheduler/strategy/A/start").status_code == 503
    assert web_client.post("/api/scheduler/strategy/A/stop").status_code == 503
    assert web_client.get("/api/scheduler/history").json()["history"] == []
