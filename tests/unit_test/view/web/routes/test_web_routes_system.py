"""
시스템 상태 및 캐시 모니터링 관련 테스트.
GET /api/cache/status, GET /api/background/status, GET /api/subscriptions/status
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
    call_kwargs = mock_web_ctx.get_cache_stats.call_args.kwargs
    assert call_kwargs["expand"] is False
    assert "latest_trading_date" in call_kwargs

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


# ── POST /api/background/ranking/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_ranking_update_success(web_client, mock_web_ctx):
    """특정 태스크(예: ranking_refresh) 강제 실행 성공 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_collect = AsyncMock()
    
    mock_web_ctx.ranking_task = mock_task

    response = web_client.post("/api/background/ranking/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    # 백그라운드 Task가 실행될 수 있도록 제어권 양보
    await asyncio.sleep(0)
    mock_task.force_collect.assert_called_once()

@pytest.mark.asyncio
async def test_force_ranking_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.ranking_task = mock_task

    response = web_client.post("/api/background/ranking/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_ranking_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.ranking_task = None
    response = web_client.post("/api/background/ranking/force-update")
    assert response.status_code == 503


# ── POST /api/background/daily-price/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_daily_price_update_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_collect = AsyncMock()
    mock_web_ctx.daily_price_collector_task = mock_task

    response = web_client.post("/api/background/daily-price/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_collect.assert_called_once()

@pytest.mark.asyncio
async def test_force_daily_price_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.daily_price_collector_task = mock_task

    response = web_client.post("/api/background/daily-price/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_daily_price_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.daily_price_collector_task = None
    response = web_client.post("/api/background/daily-price/force-update")
    assert response.status_code == 503


# ── POST /api/background/watchlist/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_watchlist_update_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_generate = AsyncMock()
    mock_web_ctx.premium_watchlist_generator_task = mock_task

    response = web_client.post("/api/background/watchlist/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_generate.assert_called_once()

@pytest.mark.asyncio
async def test_force_watchlist_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.premium_watchlist_generator_task = mock_task

    response = web_client.post("/api/background/watchlist/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_watchlist_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.premium_watchlist_generator_task = None
    response = web_client.post("/api/background/watchlist/force-update")
    assert response.status_code == 503


# ── GET /api/subscriptions/status ─────────────────────────────────────────

def test_get_subscription_status_no_service(web_client, mock_web_ctx):
    """price_subscription_service가 None이면 data: null을 반환한다."""
    mock_web_ctx.subscription_service = None

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] is None


def test_get_subscription_status_basic(web_client, mock_web_ctx):
    """구독 현황 기본 구조 및 우선순위별 종목 반환을 검증한다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "total_count": 2,
        "pt_count": 0,
        "price_count": 2,
        "max_subscriptions": 35,
        "pt_codes": [],
        "price_codes": ["005930", "035720"],
        "by_priority": {
            "CRITICAL": [],
            # Corrected to list of dicts as per RealtimeSubscriptionService.get_status()
            "HIGH":   [{"code": "005930", "active": True, "subscribed_at": None}],
            "MEDIUM": [{"code": "035720", "active": True, "subscribed_at": None}],
            "LOW":    [],
        },
    }
    mock_web_ctx.subscription_service = mock_svc

    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda c: {
        "005930": "삼성전자",
        "035720": "카카오",
    }.get(c, c)

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["max_subscriptions"] == 35

    high = data["HIGH"]
    assert len(high) == 1
    assert high[0]["code"] == "005930"
    assert high[0]["name"] == "삼성전자"
    assert high[0]["active"] is True
    assert high[0]["received_at"] is None

    medium = data["MEDIUM"]
    assert medium[0]["code"] == "035720"
    assert medium[0]["name"] == "카카오"
    assert medium[0]["active"] is True

    assert data["LOW"] == []


def test_get_subscription_status_received_at_populated(web_client, mock_web_ctx):
    """캐시에 received_at이 있으면 응답에 포함된다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "total_count": 1,
        "pt_count": 0,
        "price_count": 1,
        "max_subscriptions": 35,
        "pt_codes": [],
        "price_codes": ["005930"],
        "by_priority": {
            "CRITICAL": [],
            # Corrected to list of dicts
            "HIGH":   [{"code": "005930", "active": True, "subscribed_at": 1700000000.0}],
            "MEDIUM": [],
            "LOW":    [],
        },
    }
    mock_web_ctx.subscription_service = mock_svc

    mock_web_ctx.streaming_service.get_cached_realtime_price = MagicMock(return_value={
        "price": "70000",
        "received_at": 1700000000.0,
    })
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "삼성전자"

    response = web_client.get("/api/subscriptions/status")

    data = response.json()["data"]
    assert data["HIGH"][0]["received_at"] == 1700000000.0


def test_get_subscription_status_inactive_code(web_client, mock_web_ctx):
    """active_price_codes에 없는 종목은 active=False로 반환된다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "total_count": 0,
        "pt_count": 0,
        "price_count": 0,
        "max_subscriptions": 35,
        "pt_codes": [],
        "price_codes": [],
        "by_priority": {
            "CRITICAL": [],
            "HIGH":   [],
            "MEDIUM": [],
            # Corrected to list of dicts
            "LOW":    [{"code": "000660", "active": False, "subscribed_at": None}],
        },
    }
    mock_web_ctx.subscription_service = mock_svc
    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "SK하이닉스"

    response = web_client.get("/api/subscriptions/status")

    data = response.json()["data"]
    assert data["LOW"][0]["active"] is False
