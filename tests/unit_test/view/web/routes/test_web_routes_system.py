"""
시스템 상태 및 캐시 모니터링 관련 테스트.
GET /api/cache/status, GET /api/background/status
"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock


def test_get_cache_status(web_client, mock_web_ctx):
    """GET /api/cache/status 엔드포인트 테스트"""
    mock_web_ctx.get_cache_stats.return_value = {
        "hits": 100, "misses": 5, "hit_rate": 95.24, "total_requests": 105, "current_size": 50
    }

    response = web_client.get("/api/cache/status")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["success"] is True
    assert json_resp["data"]["hits"] == 100
    assert json_resp["data"]["hit_rate"] == 95.24


def test_get_cache_status_no_stats(web_client, mock_web_ctx):
    """get_cache_stats가 None을 반환할 때 items가 빈 리스트로 초기화되는지 테스트"""
    mock_web_ctx.get_cache_stats.return_value = None

    response = web_client.get("/api/cache/status")
    
    assert response.status_code == 200
    data = response.json()["data"]
    assert "items" in data
    assert data["items"] == []


def test_get_cache_status_with_items_and_name_mapping(web_client, mock_web_ctx):
    """items 내부의 code를 기반으로 종목명을 정상적으로 매핑하는지 테스트"""
    mock_web_ctx.get_cache_stats.return_value = {
        "items": [
            {"code": 5930, "price": 70000},  # int 타입, 0 패딩 필요
            {"code": "000660", "price": 120000},  # str 타입
            {"no_code": "here"},  # code가 없는 경우
            {"code": "999999"}  # 이름 매핑이 없는 경우 (코드 반환 확인용)
        ]
    }
    
    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda code: {
        "005930": "삼성전자",
        "000660": "SK하이닉스"
    }.get(code, None)

    response = web_client.get("/api/cache/status")
    assert response.status_code == 200
    
    items = response.json()["data"]["items"]
    assert items[0]["name"] == "삼성전자"
    assert items[1]["name"] == "SK하이닉스"
    assert "name" not in items[2]
    assert items[3]["name"] == "999999"  # None일 경우 원래 code_str이 할당됨


def test_get_cache_status_expand_false(web_client, mock_web_ctx):
    """expand=False 파라미터가 get_cache_stats에 올바르게 전달되는지 테스트"""
    mock_web_ctx.get_cache_stats.return_value = {"hits": 10}
    
    response = web_client.get("/api/cache/status?expand=false")
    
    assert response.status_code == 200
    mock_web_ctx.get_cache_stats.assert_called_once_with(expand=False)

# ── GET /api/background/status ──────────────────────────────────────────────


def test_get_background_status_no_scheduler(web_client, mock_web_ctx):
    """background_scheduler가 None이면 빈 리스트 반환."""
    mock_web_ctx.background_scheduler = None

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    json_resp = response.json()
    assert json_resp["success"] is True
    assert json_resp["data"] == []


def test_get_background_status_empty_scheduler(web_client, mock_web_ctx):
    """등록된 태스크가 없으면 빈 리스트 반환."""
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = []

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_get_background_status_returns_task_info(web_client, mock_web_ctx):
    """태스크 이름·상태·우선순위·진행률이 응답에 포함된다."""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {
        "running": True, "processed": 100, "total": 500,
        "collected": 98, "elapsed": 12.5,
    }

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "ranking_refresh", "state": "running", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    item = data[0]
    assert item["name"] == "ranking_refresh"
    assert item["state"] == "running"
    assert item["priority"] == 100
    assert item["progress"]["running"] is True
    assert item["progress"]["processed"] == 100
    assert item["progress"]["total"] == 500


def test_get_background_status_task_not_found_gives_none_progress(web_client, mock_web_ctx):
    """get_task()가 None 반환 시 해당 항목의 progress는 None."""
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "ranking_refresh", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = None

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["progress"] is None


def test_get_background_status_multiple_tasks(web_client, mock_web_ctx):
    """여러 태스크의 다양한 progress 형태를 올바르게 반환한다."""
    def _task(progress_data):
        t = MagicMock()
        t.get_progress.return_value = progress_data
        return t

    tasks_map = {
        "ranking_refresh": _task({
            "running": False, "processed": 2500, "total": 2500,
            "collected": 2498, "elapsed": 120.0,
        }),
        "websocket_watchdog": _task({
            "running": True, "subscribed_codes": 3,
            "data_gap_sec": 25.0, "market_open": True,
        }),
        "strategy_scheduler": _task({
            "running": True, "active_strategies": 2, "total_strategies": 3,
        }),
    }

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": name, "state": "running", "priority": 50}
        for name in tasks_map
    ]
    mock_web_ctx.background_scheduler.get_task.side_effect = lambda name: tasks_map.get(name)

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 3

    by_name = {item["name"]: item for item in data}

    # 배치 태스크 진행률
    assert by_name["ranking_refresh"]["progress"]["total"] == 2500
    assert by_name["ranking_refresh"]["progress"]["running"] is False

    # 웹소켓 워치독: market_open 포함
    assert by_name["websocket_watchdog"]["progress"]["market_open"] is True
    assert by_name["websocket_watchdog"]["progress"]["subscribed_codes"] == 3

    # 전략 스케줄러: 전략 카운트
    assert by_name["strategy_scheduler"]["progress"]["active_strategies"] == 2
    assert by_name["strategy_scheduler"]["progress"]["total_strategies"] == 3


def test_get_background_status_calls_get_progress_via_interface(web_client, mock_web_ctx):
    """background_scheduler.get_task(name).get_progress()가 호출되는지 검증."""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "ohlcv_update", "state": "running", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    web_client.get("/api/background/status")

    mock_web_ctx.background_scheduler.get_task.assert_called_once_with("ohlcv_update")
    mock_task.get_progress.assert_called_once()


# ── POST /api/background/force-update/{task_name} ─────────────────────────

@pytest.mark.asyncio
async def test_force_task_update_success(web_client, mock_web_ctx):
    """특정 태스크(예: ranking_refresh) 강제 실행 성공 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_collect = AsyncMock()
    
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.post("/api/background/force-update/ranking_refresh")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_web_ctx.background_scheduler.get_task.assert_called_once_with("ranking_refresh")
    
    # 백그라운드 Task가 실행될 수 있도록 제어권 양보
    await asyncio.sleep(0)
    mock_task.force_collect.assert_called_once()


@pytest.mark.asyncio
async def test_force_task_update_already_running(web_client, mock_web_ctx):
    """이미 진행 중인 태스크 강제 실행 시 409 반환 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.post("/api/background/force-update/daily_price_update")
    assert response.status_code == 409
    assert "진행 중" in response.json()["detail"]


@pytest.mark.asyncio
async def test_force_task_update_not_found(web_client, mock_web_ctx):
    """존재하지 않는 태스크 이름 요청 시 404 반환 테스트"""
    mock_web_ctx.background_scheduler.get_task.return_value = None

    response = web_client.post("/api/background/force-update/invalid_task")
    assert response.status_code == 404
    assert "찾을 수 없습니다" in response.json()["detail"]


@pytest.mark.asyncio
async def test_force_task_update_unsupported(web_client, mock_web_ctx):
    """강제 실행 메서드가 없는 태스크 요청 시 400 반환 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    del mock_task.force_collect
    del mock_task.force_run
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.post("/api/background/force-update/watchlist_update")
    assert response.status_code == 400
    assert "지원하지 않는" in response.json()["detail"]


@pytest.mark.asyncio
async def test_force_task_update_no_scheduler(web_client, mock_web_ctx):
    """스케줄러 미초기화 시 503 반환 테스트"""
    mock_web_ctx.background_scheduler = None

    response = web_client.post("/api/background/force-update/ranking_refresh")
    assert response.status_code == 503
    assert "초기화되지 않았습니다" in response.json()["detail"]
