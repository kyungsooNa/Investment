"""
시스템 상태 및 캐시 모니터링 관련 테스트.
GET /api/cache/status, GET /api/background/status, GET /api/subscriptions/status
"""
import asyncio
import pytest
import time
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from core.account_snapshot import AccountSnapshot
from repositories.streaming_stock_repo import StreamingType
from services.program_trading_stream_service import ProgramTradingStreamService
from view.web import api_common


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


def test_get_cache_status_no_mcs(web_client, mock_web_ctx):
    """ctx._mcs가 None인 경우 get_cache_stats 호출 시 latest_trading_date가 None으로 전달되는지 확인"""
    mock_web_ctx._mcs = None
    mock_web_ctx.get_cache_stats.return_value = {"items": []}

    response = web_client.get("/api/cache/status")
    
    assert response.status_code == 200
    call_kwargs = mock_web_ctx.get_cache_stats.call_args.kwargs
    assert call_kwargs["latest_trading_date"] is None


# ── GET /api/debug/requests ─────────────────────────────────────────────────

def test_get_active_requests(web_client, mock_web_ctx):
    """GET /api/debug/requests 엔드포인트 정상 작동 테스트"""
    # 활성 요청 모의 데이터
    mock_requests = {
        "req1": {"path": "/api/test1", "method": "GET", "start": time.monotonic() - 2.5, "query": ""},
        "req2": {"path": "/api/test2", "method": "POST", "start": time.monotonic() - 0.5, "query": "param=1"}
    }
    
    mock_fg = MagicMock()
    mock_fg.active_count = 1
    mock_fg.is_active = True
    mock_web_ctx.foreground_scheduler = mock_fg
    
    original_requests = getattr(api_common, "_active_requests", {})
    api_common._active_requests = mock_requests
    
    try:
        response = web_client.get("/api/debug/requests")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["foreground"]["active_count"] == 1
        assert data["foreground"]["is_blocking_background"] is True
        assert len(data["data"]) == 2
        # elapsed_sec로 정렬되므로 req1이 첫 번째여야 함
        assert data["data"][0]["path"] == "/api/test1"
    finally:
        api_common._active_requests = original_requests

def test_get_active_requests_no_ctx(web_client, monkeypatch):
    """GET /api/debug/requests 엔드포인트 _get_ctx 예외 발생(hang 상태 시뮬레이션) 테스트"""
    from view.web.routes import system
    monkeypatch.setattr(system, "_get_ctx", MagicMock(side_effect=Exception("No ctx")))
    
    original_requests = getattr(api_common, "_active_requests", {})
    api_common._active_requests = {}
    
    try:
        response = web_client.get("/api/debug/requests")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 0
        assert data["foreground"]["active_count"] == 0
        assert data["foreground"]["is_blocking_background"] is False
    finally:
        api_common._active_requests = original_requests


# ── GET /api/system/operations/status ───────────────────────────────────────


def test_get_operations_status_partial_services(web_client, mock_web_ctx):
    """운영 요약은 일부 서비스가 없어도 기본값으로 응답한다."""
    mock_web_ctx.scheduler = None
    mock_web_ctx.order_execution_service = None
    mock_web_ctx.virtual_trade_service = None
    mock_web_ctx.data_quality_service = None
    mock_web_ctx.websocket_watchdog_task = None
    mock_web_ctx.kill_switch_service = None
    mock_web_ctx.notification_service = None
    mock_web_ctx.broker = None
    mock_web_ctx.after_market_reconcile_task = None
    mock_web_ctx.account_snapshot_cache = None

    response = web_client.get("/api/system/operations/status")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["active_strategy_count"] == 0
    assert data["position_count"] == 0
    assert data["orders"]["active_order_count"] == 0
    assert data["data_quality"]["enabled"] is False
    assert data["websocket"]["receive_alive"] is False
    assert data["after_market_reconcile"] is None
    assert data["pnl"]["realized"]["realized_pnl_won"] is None
    assert data["pnl"]["evaluation"]["estimated_unrealized_pnl_won"] is None
    assert data["api_budget"] is None


def test_get_operations_status_includes_api_budget_snapshot(web_client, mock_web_ctx):
    limiter = MagicMock()
    limiter.snapshot.return_value = {
        "quotation_price": {
            "limit": 4,
            "rate_limit_per_sec": 8.0,
            "active": 2,
            "acquired_total": 10,
            "max_observed_active": 3,
        },
        "account_reconciliation": {
            "limit": 1,
            "rate_limit_per_sec": 2.0,
            "active": 0,
            "acquired_total": 2,
            "max_observed_active": 1,
        },
    }
    mock_web_ctx.api_budget_limiter = limiter

    response = web_client.get("/api/system/operations/status")

    assert response.status_code == 200
    api_budget = response.json()["data"]["api_budget"]
    assert api_budget["quotation_price"]["active"] == 2
    assert api_budget["account_reconciliation"]["rate_limit_per_sec"] == 2.0
    limiter.snapshot.assert_called_once()


def test_get_operations_status_pnl_breakdown(web_client, mock_web_ctx):
    vts = MagicMock()
    vts.get_summary.return_value = {"total_trades": 3, "win_rate": 50.0, "avg_return": 1.2}
    vts.get_all_trades.return_value = [
        {"status": "SOLD", "buy_price": 1000, "sell_price": 1200, "qty": 2},
        {"status": "HOLD", "buy_price": 2000, "qty": 1},
    ]
    vts.get_holds.return_value = [
        {"buy_price": 2000, "qty": 1},
        {"buy_price": 3000, "qty": 2},
    ]
    data = {
        "daily": {
            "2026-04-29": {"ALL": 1.0},
            "2026-04-30": {"ALL": 2.5},
        }
    }
    vts._load_data.return_value = data
    vts.get_daily_change.return_value = (1.5, "2026-04-29")
    mock_web_ctx.virtual_trade_service = vts
    mock_web_ctx.account_snapshot_cache = MagicMock()
    mock_web_ctx.account_snapshot_cache._snapshot = AccountSnapshot(
        total_equity=10_000_000,
        available_cash=3_000_000,
        positions={"005930": 4_000_000, "000660": 2_500_000},
        fetched_at=datetime(2026, 4, 30, 15, 30),
    )

    response = web_client.get("/api/system/operations/status")

    assert response.status_code == 200
    pnl = response.json()["data"]["pnl"]
    assert pnl["realized"]["realized_pnl_won"] == 400
    assert pnl["realized"]["summary"]["avg_return"] == 1.2
    assert pnl["evaluation"]["broker_total_equity"] == 10_000_000
    assert pnl["evaluation"]["position_eval_amount"] == 6_500_000
    assert pnl["evaluation"]["virtual_holding_buy_amount"] == 8_000
    assert pnl["evaluation"]["estimated_unrealized_pnl_won"] == 6_492_000
    assert pnl["day"]["daily_change_pct"] == 1.5
    assert pnl["day"]["baseline_date"] == "2026-04-29"


def test_get_operations_status_includes_after_market_reconcile(web_client, mock_web_ctx):
    task = MagicMock()
    task.get_progress.return_value = {
        "running": False,
        "last_result": {"date": "20260430", "mismatch_count": 0, "error": None},
        "history_count": 1,
    }
    mock_web_ctx.after_market_reconcile_task = task

    response = web_client.get("/api/system/operations/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["after_market_reconcile"]["last_result"]["date"] == "20260430"


def test_get_reconcile_history(web_client, mock_web_ctx):
    task = MagicMock()
    task.get_history.return_value = [{"date": "20260430", "mismatch_count": 1, "error": None}]
    mock_web_ctx.after_market_reconcile_task = task

    response = web_client.get("/api/system/reconcile/history?count=10")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"][0]["mismatch_count"] == 1
    task.get_history.assert_called_once_with(count=10)


def test_get_data_quality_history(web_client, mock_web_ctx):
    dq = MagicMock()
    dq.get_violation_history.return_value = [{"code": "005930", "reason": "stale_price"}]
    mock_web_ctx.data_quality_service = dq

    response = web_client.get("/api/system/data-quality/history?count=5&code=005930&reason=stale_price")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"][0]["reason"] == "stale_price"
    dq.get_violation_history.assert_called_once_with(count=5, code="005930", reason="stale_price")


def test_get_data_quality_history_missing_service(web_client, mock_web_ctx):
    mock_web_ctx.data_quality_service = None

    response = web_client.get("/api/system/data-quality/history")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": []}


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


def test_get_background_status_includes_program_trading_monitor(web_client, mock_web_ctx, tmp_path):
    """ProgramTradingStreamService 내부 루프도 운영 task 목록에 노출한다."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            ProgramTradingStreamService,
            "_get_base_dir",
            lambda self: str(tmp_path / "program_subscribe"),
        )
        svc = ProgramTradingStreamService(logger=MagicMock())

    try:
        svc._flush_task = MagicMock()
        svc._flush_task.done.return_value = False
        mock_web_ctx.program_trading_stream_service = svc
        mock_web_ctx.background_scheduler = MagicMock()
        mock_web_ctx.background_scheduler.get_all_status.return_value = []

        response = web_client.get("/api/background/status")
    finally:
        if svc._conn:
            svc._conn.close()
        svc._executor.shutdown(wait=False)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["name"] == "program_trading_monitor"
    assert data[0]["schedule_type"] == "always_on"
    assert data[0]["state"] == "running"
    assert data[0]["progress"]["flush_loop_alive"] is True


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
    assert item["delay_sec"] == 300  # 5분 * 60초
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
    assert by_name["ranking_refresh"]["delay_sec"] == 300  # 5분

    # 웹소켓 워치독: market_open 포함
    assert by_name["websocket_watchdog"]["progress"]["market_open"] is True
    assert by_name["websocket_watchdog"]["progress"]["subscribed_codes"] == 3
    assert by_name["websocket_watchdog"]["delay_sec"] == 0  # 딜레이 없음

    # 전략 스케줄러: 전략 카운트
    assert by_name["strategy_scheduler"]["progress"]["active_strategies"] == 2
    assert by_name["strategy_scheduler"]["progress"]["total_strategies"] == 3
    assert by_name["strategy_scheduler"]["delay_sec"] == 0  # 딜레이 없음


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


def test_get_background_status_module_names(web_client, mock_web_ctx):
    """태스크의 모듈 경로에 따라 올바른 schedule_type이 지정되는지 테스트"""
    
    class TaskAlwaysOn: pass
    TaskAlwaysOn.__module__ = "task.background.always_on.notifier"
    
    class TaskAfterMarket: pass
    TaskAfterMarket.__module__ = "task.background.after_market.ranking"

    class TaskIntraday: pass
    TaskIntraday.__module__ = "task.background.intraday.collector"

    class TaskStrategyScheduler: pass
    TaskStrategyScheduler.__module__ = "task.background.strategy_scheduler"

    class TaskUnknown: pass
    TaskUnknown.__module__ = "task.background.some_other.module"

    tasks_map = {
        "t1": TaskAlwaysOn(),
        "t2": TaskAfterMarket(),
        "t3": TaskIntraday(),
        "t4": TaskStrategyScheduler(),
        "t5": TaskUnknown(),
    }
    for t in tasks_map.values():
        t.get_progress = MagicMock(return_value=None)

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": name, "state": "running", "priority": 1}
        for name in tasks_map.keys()
    ]
    mock_web_ctx.background_scheduler.get_task.side_effect = lambda name: tasks_map.get(name)

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    data = response.json()["data"]
    
    types = {item["name"]: item["schedule_type"] for item in data}
    assert types["t1"] == "always_on"
    assert types["t2"] == "after_market"
    assert types["t3"] == "intraday"
    assert types["t4"] == "intraday"
    assert types["t5"] == "unknown"

    # 모든 태스크명이 task_config.yaml에 없으므로 delay_sec은 0
    delays = {item["name"]: item["delay_sec"] for item in data}
    assert all(v == 0 for v in delays.values())


def test_get_background_status_theme_classification_is_after_market(web_client, mock_web_ctx):
    """테마 분류 태스크는 장마감 후 배치로 분류된다."""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "theme_classification", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["name"] == "theme_classification"
    assert item["schedule_type"] == "after_market"


def test_get_background_status_pre_market_health_check_schedule_type(web_client, mock_web_ctx):
    """pre_market_health_check는 IDLE이어도 실제 점검 progress를 반환한다."""
    class PreMarketTask:
        pass

    PreMarketTask.__module__ = "task.background.intraday.pre_market_health_check_task"
    mock_task = PreMarketTask()
    mock_task.get_progress = MagicMock(
        return_value={
            "running": False,
            "last_checked_date": "20260526",
            "last_result": {"ok": True, "issues": []},
        }
    )

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "pre_market_health_check", "state": "idle", "priority": 50},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["name"] == "pre_market_health_check"
    assert data[0]["schedule_type"] == "pre_market"
    assert data[0]["progress"] == {
        "running": False,
        "last_checked_date": "20260526",
        "last_result": {"ok": True, "issues": []},
    }
    mock_task.get_progress.assert_called_once()


def test_get_background_status_cache_warmup_schedule_type(web_client, mock_web_ctx):
    """cache_warmup은 장마감 티켓 배치가 아니라 장전/장중 웜업으로 분류한다."""
    class CacheWarmupTask:
        pass

    CacheWarmupTask.__module__ = "task.background.after_market.cache_warmup_task"
    mock_task = CacheWarmupTask()
    mock_task._progress = {"running": False}
    mock_task.get_progress = MagicMock(return_value={"running": False, "last_warmed_date": None})

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "cache_warmup", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["name"] == "cache_warmup"
    assert data[0]["schedule_type"] == "pre_market"


def test_get_background_status_idle_with_internal_flag(web_client, mock_web_ctx):
    """태스크가 IDLE 상태지만 내부 플래그(_is_refreshing)가 있는 경우 get_progress() 호출 확인"""
    mock_task = MagicMock()
    mock_task._is_refreshing = True
    mock_task.get_progress.return_value = {"running": True, "processed": 10}
    
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "some_idle_task", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["progress"] == {"running": True, "processed": 10}
    mock_task.get_progress.assert_called_once()


def test_get_background_status_idle_with_internal_flag_error(web_client, mock_web_ctx):
    """태스크가 IDLE 상태이고 내부 플래그(_progress)가 있는데 get_progress()가 에러를 낼 경우"""
    mock_task = MagicMock()
    mock_task._progress = {}
    mock_task.get_progress.side_effect = Exception("test error")
    
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "some_idle_task", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["progress"] == {"running": False, "error": "test error"}


def test_get_background_status_idle_without_progress_flag(web_client, mock_web_ctx):
    """IDLE task without an internal progress flag returns a safe waiting placeholder."""
    mock_task = MagicMock()
    if hasattr(mock_task, "_progress"):
        delattr(mock_task, "_progress")
    mock_task._is_refreshing = False

    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "some_idle_task", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["progress"] == {"running": False, "status": "Waiting to start"}
    mock_task.get_progress.assert_not_called()


def test_background_status_time_dispatcher_exception_is_ignored(web_client, mock_web_ctx):
    """TimeDispatcher status errors should not break the background status endpoint."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx.time_dispatcher = MagicMock()
    mock_web_ctx.time_dispatcher.get_status.side_effect = Exception("boom")

    response = web_client.get("/api/background/status")

    assert response.status_code == 200
    assert response.json()["time_dispatcher"] is None


def test_get_background_status_running_error(web_client, mock_web_ctx):
    """태스크가 RUNNING 상태에서 get_progress()가 예외를 발생시키는 경우 처리 확인"""
    mock_task = MagicMock()
    mock_task.get_progress.side_effect = Exception("running error")
    
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "some_running_task", "state": "running", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["progress"] == {"running": False, "error": "running error"}


# ── POST /api/background/ranking/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_ranking_update_success(web_client, mock_web_ctx):
    """특정 태스크(예: ranking_refresh) 강제 실행 성공 테스트"""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    
    mock_web_ctx.ranking_task = mock_task

    response = web_client.post("/api/background/ranking/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    # 백그라운드 Task가 실행될 수 있도록 제어권 양보
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

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
    mock_task.force_run = AsyncMock()
    mock_web_ctx.daily_price_collector_task = mock_task

    response = web_client.post("/api/background/daily-price/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

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
    mock_task.force_run = AsyncMock()
    mock_web_ctx.premium_watchlist_generator_task = mock_task

    response = web_client.post("/api/background/watchlist/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

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


# ── POST /api/background/cache-warmup/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_cache_warmup_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    mock_web_ctx.cache_warmup_task = mock_task

    response = web_client.post("/api/background/cache-warmup/force-update")
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

@pytest.mark.asyncio
async def test_force_cache_warmup_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.cache_warmup_task = mock_task

    response = web_client.post("/api/background/cache-warmup/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_cache_warmup_not_init(web_client, mock_web_ctx):
    mock_web_ctx.cache_warmup_task = None
    response = web_client.post("/api/background/cache-warmup/force-update")
    assert response.status_code == 503


# ── POST /api/background/newhigh/force-update ─────────────────────────

@pytest.mark.asyncio
async def test_force_newhigh_update_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    mock_web_ctx.newhigh_task = mock_task

    response = web_client.post("/api/background/newhigh/force-update")
    
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

@pytest.mark.asyncio
async def test_force_newhigh_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.newhigh_task = mock_task

    response = web_client.post("/api/background/newhigh/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_newhigh_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.newhigh_task = None
    response = web_client.post("/api/background/newhigh/force-update")
    assert response.status_code == 503


# ── POST /api/background/theme-classification/force-update ─────────────

@pytest.mark.asyncio
async def test_force_theme_classification_update_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    mock_web_ctx.theme_classification_task = mock_task

    response = web_client.post("/api/background/theme-classification/force-update")

    assert response.status_code == 200
    assert response.json()["success"] is True

    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()


@pytest.mark.asyncio
async def test_force_theme_classification_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.theme_classification_task = mock_task

    response = web_client.post("/api/background/theme-classification/force-update")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_force_theme_classification_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.theme_classification_task = None
    response = web_client.post("/api/background/theme-classification/force-update")
    assert response.status_code == 503


# ── POST /api/background/minervini/force-update ───────────────────────

@pytest.mark.asyncio
async def test_force_minervini_update_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    mock_web_ctx.minervini_update_task = mock_task

    response = web_client.post("/api/background/minervini/force-update")
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()

@pytest.mark.asyncio
async def test_force_minervini_update_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.minervini_update_task = mock_task

    response = web_client.post("/api/background/minervini/force-update")
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_force_minervini_update_not_init(web_client, mock_web_ctx):
    mock_web_ctx.minervini_update_task = None
    response = web_client.post("/api/background/minervini/force-update")
    assert response.status_code == 503


# ── POST /api/background/reconcile/force-update ───────────────────────

@pytest.mark.asyncio
async def test_force_after_market_reconcile_success(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False}
    mock_task.force_run = AsyncMock()
    mock_web_ctx.after_market_reconcile_task = mock_task

    response = web_client.post("/api/background/reconcile/force-update")

    assert response.status_code == 200
    assert response.json()["success"] is True

    await asyncio.sleep(0)
    mock_task.force_run.assert_called_once()


@pytest.mark.asyncio
async def test_force_after_market_reconcile_running(web_client, mock_web_ctx):
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": True}
    mock_web_ctx.after_market_reconcile_task = mock_task

    response = web_client.post("/api/background/reconcile/force-update")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_force_after_market_reconcile_not_init(web_client, mock_web_ctx):
    mock_web_ctx.after_market_reconcile_task = None

    response = web_client.post("/api/background/reconcile/force-update")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_force_strategy_log_report_success_running_and_not_init(web_client, mock_web_ctx, monkeypatch):
    from view.web.routes import system

    mock_web_ctx.strategy_log_report_task = None
    response = web_client.post("/api/background/strategy-log-report/force-update")
    assert response.status_code == 503

    running_task = MagicMock()
    running_task.get_progress.return_value = {"running": True}
    mock_web_ctx.strategy_log_report_task = running_task
    response = web_client.post("/api/background/strategy-log-report/force-update")
    assert response.status_code == 409

    task = MagicMock()
    task.get_progress.return_value = {"running": False}
    task.force_run = AsyncMock()
    mock_web_ctx.strategy_log_report_task = task

    def fake_create_task(coro):
        coro.close()
        return MagicMock()

    monkeypatch.setattr(system.asyncio, "create_task", fake_create_task)
    response = web_client.post("/api/background/strategy-log-report/force-update")

    assert response.status_code == 200
    assert response.json()["success"] is True


# ── GET /api/background/status — time_dispatcher 티켓 발행 현황 ──────────────

def _make_td_mock(last_dispatched_date, last_dispatched_at=None, market_is_open=False, registered_tasks=None):
    """TimeDispatcher mock 생성 헬퍼."""
    td = MagicMock()
    td.get_status.return_value = {
        "last_dispatched_date": last_dispatched_date,
        "last_dispatched_at": last_dispatched_at,
        "market_is_open": market_is_open,
        "registered_tasks": registered_tasks or [
            {"name": "ranking_refresh", "priority": 100, "delay_sec": 0},
            {"name": "daily_price_collector", "priority": 50, "delay_sec": 1800},
        ],
    }
    return td


def test_background_status_time_dispatcher_none_when_get_status_not_dict(web_client, mock_web_ctx):
    """ctx.time_dispatcher가 있어도 get_status()가 dict를 반환하지 않으면 time_dispatcher는 null."""
    mock_web_ctx.background_scheduler = None
    # MagicMock().get_status() → MagicMock (not dict) → time_dispatcher_info stays None
    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    assert response.json()["time_dispatcher"] is None


def test_background_status_time_dispatcher_ticket_issued_today(web_client, mock_web_ctx):
    """last_dispatched_date == latest_trading_date이면 ticket_issued_today=True, market_is_open 포함."""
    mock_web_ctx.background_scheduler = None
    # conftest: _mcs.get_latest_trading_date returns "20260326"
    mock_web_ctx.time_dispatcher = _make_td_mock(
        last_dispatched_date="20260326",
        last_dispatched_at=1234567890.0,
        market_is_open=False,
    )

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    td = response.json()["time_dispatcher"]
    assert td is not None
    assert td["ticket_issued_today"] is True
    assert td["last_dispatched_date"] == "20260326"
    assert td["last_dispatched_at"] == 1234567890.0
    assert td["latest_trading_date"] == "20260326"
    assert td["market_is_open"] is False
    assert len(td["registered_tasks"]) == 2


def test_background_status_time_dispatcher_ticket_not_issued(web_client, mock_web_ctx):
    """last_dispatched_date != latest_trading_date이면 ticket_issued_today=False."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx.time_dispatcher = _make_td_mock(last_dispatched_date="20260325")

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    td = response.json()["time_dispatcher"]
    assert td["ticket_issued_today"] is False
    assert td["last_dispatched_date"] == "20260325"
    assert td["latest_trading_date"] == "20260326"


def test_background_status_time_dispatcher_never_dispatched(web_client, mock_web_ctx):
    """last_dispatched_date가 None이면 ticket_issued_today=False."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx.time_dispatcher = _make_td_mock(last_dispatched_date=None)

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    td = response.json()["time_dispatcher"]
    assert td["ticket_issued_today"] is False
    assert td["last_dispatched_date"] is None


def test_background_status_time_dispatcher_no_mcs(web_client, mock_web_ctx):
    """_mcs가 None이면 latest_trading_date=None, ticket_issued_today=False."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx._mcs = None
    mock_web_ctx.time_dispatcher = _make_td_mock(last_dispatched_date="20260326")

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    td = response.json()["time_dispatcher"]
    assert td["latest_trading_date"] is None
    assert td["ticket_issued_today"] is False


def test_background_status_time_dispatcher_market_is_open_true(web_client, mock_web_ctx):
    """market_is_open=True이면 응답에 그대로 반영된다 (장중 상태)."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx.time_dispatcher = _make_td_mock(
        last_dispatched_date="20260325",  # 어제 날짜 → 오늘 미발행
        market_is_open=True,
    )

    response = web_client.get("/api/background/status")
    td = response.json()["time_dispatcher"]
    assert td["market_is_open"] is True
    assert td["ticket_issued_today"] is False


def test_background_status_time_dispatcher_market_is_open_none(web_client, mock_web_ctx):
    """market_is_open=None (MarketClock 없음)이면 응답에 null로 반환된다."""
    mock_web_ctx.background_scheduler = None
    mock_web_ctx.time_dispatcher = _make_td_mock(
        last_dispatched_date=None,
        market_is_open=None,
    )

    response = web_client.get("/api/background/status")
    td = response.json()["time_dispatcher"]
    assert td["market_is_open"] is None


def test_background_status_time_dispatcher_included_with_tasks(web_client, mock_web_ctx):
    """태스크 목록과 함께 time_dispatcher가 올바르게 반환된다."""
    mock_task = MagicMock()
    mock_task.get_progress.return_value = {"running": False, "processed": 0, "total": 0}
    mock_web_ctx.background_scheduler = MagicMock()
    mock_web_ctx.background_scheduler.get_all_status.return_value = [
        {"name": "ranking_refresh", "state": "idle", "priority": 100},
    ]
    mock_web_ctx.background_scheduler.get_task.return_value = mock_task
    mock_web_ctx.time_dispatcher = _make_td_mock(
        last_dispatched_date="20260326",
        registered_tasks=[{"name": "ranking_refresh", "priority": 100, "delay_sec": 300}],
    )

    response = web_client.get("/api/background/status")
    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["name"] == "ranking_refresh"
    td = body["time_dispatcher"]
    assert td["ticket_issued_today"] is True
    assert td["registered_tasks"][0]["delay_sec"] == 300


# ── GET /api/subscriptions/status ─────────────────────────────────────────

def test_get_subscription_status_no_service(web_client, mock_web_ctx):
    """price_subscription_service가 None이면 data: null을 반환한다."""
    mock_web_ctx.price_subscription_service = None

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] is None


def test_get_subscription_status_basic(web_client, mock_web_ctx):
    """구독 현황 기본 구조 및 우선순위별 종목 반환을 검증한다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 2,
        "max_subscriptions": 40,  # 기존 35에서 40으로 업데이트됨
        "active_codes_price": ["005930"], # 호가/체결 분리
        "active_codes_pt": ["035720"],    # PT 분리
        "pending_count": 2,
        "pending_by_priority": {
            "HIGH":   ["005930"],
            "MEDIUM": ["035720"],
            "LOW":    [],
        },
    }
    mock_web_ctx.price_subscription_service = mock_svc

    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda c: {
        "005930": "삼성전자",
        "035720": "카카오",
    }.get(c, c)

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    data = response.json()["data"]
    
    assert data["active_count"] == 2
    assert data["max_subscriptions"] == 40
    assert data["pending_count"] == 2
    assert "005930" in data["active_codes_price"]
    assert "035720" in data["active_codes_pt"]

    # 구조 변경에 맞춰 pending_by_priority 하위 탐색으로 수정
    priorities = data["pending_by_priority"]
    high = priorities["HIGH"]
    assert len(high) == 1
    assert high[0]["code"] == "005930"
    assert high[0]["name"] == "삼성전자"
    assert high[0]["active"] is True
    assert high[0]["received_at"] is None

    medium = priorities["MEDIUM"]
    assert medium[0]["code"] == "035720"
    assert medium[0]["name"] == "카카오"
    assert medium[0]["active"] is True

    assert priorities["LOW"] == []


def test_get_subscription_status_received_at_populated(web_client, mock_web_ctx):
    """캐시에 received_at이 있으면 응답에 포함된다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 1,
        "max_subscriptions": 40,
        "active_codes_price": ["005930"],
        "active_codes_pt": [],
        "pending_count": 1,
        "pending_by_priority": {
            "HIGH":   ["005930"],
            "MEDIUM": [],
            "LOW":    [],
        },
    }
    mock_web_ctx.price_subscription_service = mock_svc

    mock_web_ctx.streaming_service.get_cached_realtime_price = MagicMock(return_value={
        "price": "70000",
        "received_at": 1700000000.0,
    })
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "삼성전자"

    response = web_client.get("/api/subscriptions/status")

    data = response.json()["data"]
    assert data["pending_by_priority"]["HIGH"][0]["received_at"] == 1700000000.0


def test_get_subscription_status_inactive_code(web_client, mock_web_ctx):
    """active 상태가 아닌 종목은 active=False로 반환된다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 0,
        "max_subscriptions": 40,
        "active_codes_price": [],
        "active_codes_pt": [],
        "pending_count": 1,
        "pending_by_priority": {
            "HIGH":   [],
            "MEDIUM": [],
            "LOW":    ["000660"],
        },
    }
    mock_web_ctx.price_subscription_service = mock_svc
    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "SK하이닉스"

    response = web_client.get("/api/subscriptions/status")

    data = response.json()["data"]
    assert data["pending_by_priority"]["LOW"][0]["active"] is False


def test_get_subscription_status_no_streaming_service(web_client, mock_web_ctx):
    """streaming_service가 None일 경우 received_at이 None으로 할당되는지 확인"""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 1,
        "max_subscriptions": 40,
        "active_codes_price": ["005930"],
        "active_codes_pt": [],
        "pending_count": 0,
        "pending_by_priority": {
            "HIGH":   ["005930"],
            "MEDIUM": [],
            "LOW":    [],
        },
    }
    mock_web_ctx.price_subscription_service = mock_svc
    mock_web_ctx.streaming_service = None  # streaming_service 없음 설정
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "삼성전자"

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pending_by_priority"]["HIGH"][0]["received_at"] is None


def test_get_subscription_status_uses_program_trading_repo_as_critical_source(web_client, mock_web_ctx):
    """CRITICAL은 PT 저장소의 현재 desired와 active 상태를 기준으로 노출된다."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 2,
        "max_subscriptions": 40,
        "active_codes_price": ["000660"],
        "active_codes_pt": [],
        "pending_count": 3,
        "pending_by_priority": {
            "CRITICAL": ["000660"],  # 정책 refs에 남은 오래된 PT 종목
            "HIGH": [],
            "MEDIUM": [],
            "LOW": [],
        },
    }
    mock_web_ctx.price_subscription_service = mock_svc
    mock_web_ctx.streaming_stock_repo = MagicMock()
    mock_web_ctx.streaming_stock_repo.get_desired.side_effect = lambda stream_type: (
        {"005930", "080220"} if stream_type == StreamingType.PROGRAM_TRADING else set()
    )
    mock_web_ctx.streaming_stock_repo.get_active.side_effect = lambda stream_type: (
        {"005930", "080220"} if stream_type == StreamingType.PROGRAM_TRADING else set()
    )
    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda c: {
        "000660": "SK하이닉스",
        "005930": "삼성전자",
        "080220": "제주반도체",
    }.get(c, c)

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_count"] == 3
    assert data["active_codes_pt"] == ["005930", "080220"]
    assert data["pending_count"] == 2
    critical = data["pending_by_priority"]["CRITICAL"]
    assert [row["code"] for row in critical] == ["005930", "080220"]
    assert [row["active"] for row in critical] == [True, True]


def test_get_subscription_status_ignores_repo_error(web_client, mock_web_ctx):
    """streaming_stock_repo errors are ignored and existing pending data is still returned."""
    mock_svc = MagicMock()
    mock_svc.get_status.return_value = {
        "active_count": 1,
        "max_subscriptions": 40,
        "active_codes_price": ["005930"],
        "active_codes_pt": [],
        "pending_by_priority": {"HIGH": ["005930"]},
    }
    mock_web_ctx.price_subscription_service = mock_svc
    mock_web_ctx.streaming_stock_repo = MagicMock()
    mock_web_ctx.streaming_stock_repo.get_desired.side_effect = Exception("repo down")
    mock_web_ctx.streaming_service = MagicMock()
    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = {
        "price": "70000",
        "received_at": 1700000000.0,
    }
    mock_web_ctx.stock_code_repository.get_name_by_code.return_value = "Samsung"

    response = web_client.get("/api/subscriptions/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pending_count"] == 1
    row = data["pending_by_priority"]["HIGH"][0]
    assert row["code"] == "005930"
    assert row["price"] == "70000"
    assert row["received_at"] == 1700000000.0


def test_get_subscription_debug_missing_dependency(web_client, mock_web_ctx):
    """subscriptions debug returns data null until every required service is initialized."""
    mock_web_ctx.price_stream_service = None

    response = web_client.get("/api/subscriptions/debug")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": None}


def test_get_subscription_debug_requested_codes(web_client, mock_web_ctx):
    """subscriptions debug reports detailed state for explicitly requested codes."""
    mock_sub_svc = MagicMock()
    mock_sub_svc.get_status.return_value = {
        "active_codes_price": ["005930"],
        "active_codes_pt": ["035720"],
    }
    mock_web_ctx.price_subscription_service = mock_sub_svc

    mock_web_ctx.streaming_stock_repo = MagicMock()
    mock_web_ctx.streaming_stock_repo.get_desired.side_effect = (
        lambda stream_type: {"005930"} if stream_type == StreamingType.UNIFIED_PRICE else {"035720"}
    )
    mock_web_ctx.streaming_stock_repo.get_active.side_effect = (
        lambda stream_type: {"005930"} if stream_type == StreamingType.UNIFIED_PRICE else set()
    )

    mock_web_ctx.streaming_service = MagicMock()
    mock_web_ctx.streaming_service.get_cached_realtime_price.side_effect = lambda code: {
        "005930": {"price": "70000", "received_at": 1700000000.0},
        "000660": None,
    }.get(code)
    mock_web_ctx.streaming_service.is_subscribed_realtime_price.side_effect = lambda code: code == "005930"

    mock_web_ctx.price_stream_service = MagicMock()
    mock_web_ctx.price_stream_service.get_last_tick_ts.side_effect = lambda code: 123.0 if code == "005930" else None
    mock_web_ctx.price_stream_service.get_subscription_age.side_effect = lambda code: 10.5 if code == "005930" else None

    websocket_api = MagicMock()
    websocket_api._subscribed_items = {("H0STCNT0", "005930"), ("H0STASP0", "000660")}
    websocket_api._pending_requests = {("H0STCNI0", "005930"): object(), ("H0STASP0", "035720"): object()}
    raw_client = MagicMock()
    raw_client._websocketAPI = websocket_api
    raw_client._client = None
    wrapper = MagicMock()
    wrapper._client = raw_client
    broker = MagicMock()
    broker._client = wrapper
    broker.is_websocket_receive_alive.return_value = True
    mock_web_ctx.broker = broker

    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda code: {
        "005930": "Samsung",
        "000660": "SK Hynix",
    }.get(code)

    response = web_client.get("/api/subscriptions/debug?codes=005930,%20000660,,")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["receive_alive"] is True
    assert [row["code"] for row in data["rows"]] == ["005930", "000660"]

    by_code = {row["code"]: row for row in data["rows"]}
    samsung = by_code["005930"]
    assert samsung["name"] == "Samsung"
    assert samsung["desired_price"] is True
    assert samsung["desired_pt"] is False
    assert samsung["repo_active_price"] is True
    assert samsung["policy_active_price"] is True
    assert samsung["is_subscribed_realtime_price"] is True
    assert samsung["cached_price"] == "70000"
    assert samsung["cached_received_at"] == 1700000000.0
    assert samsung["last_tick_ts"] == 123.0
    assert samsung["subscription_age_sec"] == 10.5
    assert samsung["broker_subscribed_tr_ids"] == ["H0STCNT0"]
    assert samsung["broker_pending_tr_ids"] == ["H0STCNI0"]

    hynix = by_code["000660"]
    assert hynix["name"] == "SK Hynix"
    assert hynix["cached_price"] is None
    assert hynix["broker_subscribed_tr_ids"] == ["H0STASP0"]


def test_get_subscription_debug_uses_union_when_codes_not_requested(web_client, mock_web_ctx):
    """without a codes query, debug rows are built from repo and policy code unions."""
    mock_sub_svc = MagicMock()
    mock_sub_svc.get_status.return_value = {
        "active_codes_price": ["005930"],
        "active_codes_pt": ["035720"],
    }
    mock_web_ctx.price_subscription_service = mock_sub_svc

    mock_web_ctx.streaming_stock_repo = MagicMock()
    mock_web_ctx.streaming_stock_repo.get_desired.side_effect = (
        lambda stream_type: {"000660"} if stream_type == StreamingType.UNIFIED_PRICE else {"035720"}
    )
    mock_web_ctx.streaming_stock_repo.get_active.side_effect = (
        lambda stream_type: set() if stream_type == StreamingType.UNIFIED_PRICE else {"051910"}
    )

    mock_web_ctx.streaming_service = MagicMock()
    mock_web_ctx.streaming_service.get_cached_realtime_price.return_value = None
    mock_web_ctx.streaming_service.is_subscribed_realtime_price.return_value = False
    mock_web_ctx.price_stream_service = MagicMock()
    mock_web_ctx.price_stream_service.get_last_tick_ts.return_value = None
    mock_web_ctx.price_stream_service.get_subscription_age.return_value = None
    mock_web_ctx.stock_code_repository.get_name_by_code.side_effect = lambda code: code

    raw_client = MagicMock()
    raw_client._websocketAPI = None
    raw_client._client = None
    broker = MagicMock()
    broker._client = raw_client
    broker.is_websocket_receive_alive.return_value = False
    mock_web_ctx.broker = broker

    response = web_client.get("/api/subscriptions/debug")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["receive_alive"] is False
    assert [row["code"] for row in data["rows"]] == ["000660", "005930", "035720", "051910"]
