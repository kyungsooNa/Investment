"""
스케줄러 관련 테스트 (scheduler.html).
"""
import asyncio
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

@pytest.mark.asyncio
async def test_scheduler_strategy_control_failure(web_client, mock_web_ctx):
    """스케줄러 개별 전략 제어 실패 (404) 테스트"""
    # Start Strategy - Not Found
    mock_web_ctx.scheduler.start_strategy = AsyncMock(return_value=False)
    resp = web_client.post("/api/scheduler/strategy/UnknownStrat/start")
    assert resp.status_code == 404
    assert "찾을 수 없습니다" in resp.json()["detail"]

    # Stop Strategy - Not Found
    mock_web_ctx.scheduler.stop_strategy = MagicMock(return_value=False)
    resp = web_client.post("/api/scheduler/strategy/UnknownStrat/stop")
    assert resp.status_code == 404
    assert "찾을 수 없습니다" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_get_scheduler_history_filter(web_client, mock_web_ctx):
    """스케줄러 이력 조회 필터링 테스트"""
    mock_web_ctx.scheduler.get_signal_history = MagicMock(return_value=[])
    
    # strategy 파라미터 전달
    response = web_client.get("/api/scheduler/history?strategy=TestStrat")
    assert response.status_code == 200
    mock_web_ctx.scheduler.get_signal_history.assert_called_with("TestStrat")

@pytest.mark.asyncio
async def test_get_scheduler_history_edge_cases(web_client, mock_web_ctx):
    """스케줄러 이력 조회 엣지 케이스 (매퍼 없음, 코드 없음 등)"""
    history_data = [
        {"code": "", "name": "NoCode"},
        {"code": "000000", "name": "UnknownCode"}
    ]
    mock_web_ctx.scheduler.get_signal_history.return_value = history_data
    
    # 1. Mapper가 없는 경우
    mock_web_ctx.stock_code_mapper = None
    
    response = web_client.get("/api/scheduler/history")
    assert response.status_code == 200
    assert response.json()["history"] == history_data # 변경 없음

    # 2. Mapper는 있지만 이름 조회 실패하는 경우
    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code.return_value = "" # 이름 못 찾음
    mock_web_ctx.stock_code_mapper = mock_mapper
    
    response = web_client.get("/api/scheduler/history")
    assert response.status_code == 200
    data = response.json()["history"]
    assert data[0]["name"] == "NoCode" # 코드 없어서 스킵
    assert data[1]["name"] == "UnknownCode" # 이름 못 찾아서 기존 이름 유지

@pytest.mark.asyncio
async def test_generate_osb_pool_a_success(web_client, mock_web_ctx):
    """오닐 스퀴즈 전략 Pool A 생성 성공 테스트"""
    mock_web_ctx.initialized = True
    # Ensure oneil_universe_service is a mock with async method
    mock_service = MagicMock()
    mock_service.generate_pool_a = AsyncMock(return_value={"result": "ok"})
    mock_web_ctx.oneil_universe_service = mock_service

    # URL encoding might be handled by TestClient, but explicit path is safer if needed.
    # FastAPI TestClient handles unicode paths correctly.
    response = web_client.post("/api/scheduler/strategy/오닐스퀴즈돌파/generate-pool-a")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["result"] == {"result": "ok"}
    mock_service.generate_pool_a.assert_awaited_once()

@pytest.mark.asyncio
async def test_generate_osb_pool_a_not_initialized(web_client, mock_web_ctx):
    """오닐 스퀴즈 전략 Pool A 생성 실패 (초기화 안됨)"""
    mock_web_ctx.initialized = False
    
    response = web_client.post("/api/scheduler/strategy/오닐스퀴즈돌파/generate-pool-a")
    
    assert response.status_code == 503
    assert "서비스가 초기화되지 않았습니다" in response.json()["detail"]

@pytest.mark.asyncio
async def test_generate_osb_pool_a_service_missing(web_client, mock_web_ctx):
    """오닐 스퀴즈 전략 Pool A 생성 실패 (서비스 없음)"""
    mock_web_ctx.initialized = True
    # Remove the service attribute or set to None
    mock_web_ctx.oneil_universe_service = None
    
    response = web_client.post("/api/scheduler/strategy/오닐스퀴즈돌파/generate-pool-a")

    assert response.status_code == 404
    assert "오닐 유니버스 서비스가 초기화되지 않았습니다" in response.json()["detail"]


# ── SSE 스트림 엔드포인트 테스트 ──

@pytest.mark.asyncio
async def test_scheduler_stream_no_scheduler(web_client, mock_web_ctx):
    """스케줄러 미초기화 시 SSE 스트림 응답 테스트."""
    mock_web_ctx.scheduler = None

    response = web_client.get("/api/scheduler/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_scheduler_stream_connects(web_client, mock_web_ctx):
    """SSE 스트림 연결 시 구독자 큐가 생성되는지 테스트."""
    mock_queue = asyncio.Queue()
    mock_web_ctx.scheduler.create_subscriber_queue = MagicMock(return_value=mock_queue)
    mock_web_ctx.scheduler.remove_subscriber_queue = MagicMock()

    # 큐에 데이터 넣고 None(종료 신호) 추가
    await mock_queue.put({
        "strategy_name": "TestStrat",
        "code": "005930",
        "name": "삼성전자",
        "action": "BUY",
        "price": 70000,
        "reason": "모멘텀 돌파",
        "timestamp": "2025-01-01 10:00:00",
        "api_success": True,
    })
    await mock_queue.put(None)  # 종료 신호

    response = web_client.get("/api/scheduler/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # 응답 본문에 시그널 데이터가 포함되어야 함
    assert "005930" in response.text
    assert "삼성전자" in response.text
    assert "BUY" in response.text

    # 구독자 큐 생성/제거 확인
    mock_web_ctx.scheduler.create_subscriber_queue.assert_called_once()
    mock_web_ctx.scheduler.remove_subscriber_queue.assert_called_once_with(mock_queue)
