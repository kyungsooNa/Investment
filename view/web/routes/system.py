"""
시스템 상태 및 캐시 모니터링 API 엔드포인트.
"""
import asyncio
import time
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx
import view.web.api_common as api_common

router = APIRouter()

# 태스크별 실행 스케줄 유형
# intraday: 장 중에만 의미있는 태스크 (장 마감 후 비활성)
# after_market: 장 마감 후 실행되는 배치 태스크 (장 중 비활성)
# always_on: 항상 동작해야 하는 실시간 태스크
_SCHEDULE_TYPES = {
    "websocket_watchdog":  "intraday",
    "strategy_scheduler":  "intraday",
    "ranking_refresh":     "after_market",
    "daily_price_collector": "after_market",
    "minervini_update":     "after_market",
    "ohlcv_update":        "after_market",
    "전일기준주도주_생성":  "after_market",
    "newhigh":             "after_market",
    "notification_queue_task":  "always_on",
}

_SCHEDULE_ORDER = {
    "always_on": 0,
    "intraday": 1,
    "after_market": 2,
    "unknown": 99,
}


@router.get("/cache/status")
async def get_cache_status(expand: bool = True):
    """메모리 캐시 상태 및 적중률 통계 반환"""
    ctx = _get_ctx()
    latest_trading_date = await ctx._mcs.get_latest_trading_date() if ctx._mcs else None
    # get_cache_stats()는 CPU 집약적 작업(대량 JSON 직렬화)이므로 스레드 풀에서 실행해 이벤트 루프 블로킹을 방지
    stats = await asyncio.to_thread(
        ctx.get_cache_stats, expand=expand, latest_trading_date=latest_trading_date
    ) or {}

    if "items" not in stats:
        stats["items"] = []

    if stats.get("items"):
        for item in stats["items"]:
            code = item.get("code")
            if code:
                code_str = str(code).zfill(6)  # 확실한 6자리 문자열로 변환
                name = ctx.stock_code_repository.get_name_by_code(code_str)
                item["name"] = name if name and name != code_str else code_str

    return {
        "success": True,
        "data": stats
    }


@router.get("/debug/requests")
def get_active_requests():
    """현재 서버에서 처리 중인 in-flight 요청 목록 반환 (hang 진단용).

    ForegroundScheduler가 백그라운드 태스크 중단을 기다리거나,
    무거운 응답 직렬화로 이벤트 루프가 블로킹될 때 어떤 요청이 대기 중인지 확인한다.
    이 엔드포인트 자체는 Foreground 미들웨어를 우회하므로 hang 상태에서도 응답한다.
    """
    try:
        ctx = _get_ctx()
        fg = getattr(ctx, "foreground_scheduler", None)
    except Exception:
        fg = None

    now = time.monotonic()
    active = [
        {
            "path": r["path"],
            "method": r["method"],
            "elapsed_sec": round(now - r["start"], 1),
            "query": r["query"],
        }
        for r in api_common._active_requests.values()
    ]
    active.sort(key=lambda x: x["elapsed_sec"], reverse=True)
    return {
        "success": True,
        "count": len(active),
        "foreground": {
            "active_count": fg.active_count if fg else 0,
            "is_blocking_background": fg.is_active if fg else False,
        },
        "data": active,
    }


# 1. 동기(def) 함수를 비동기(async def) 함수로 변경하여 이벤트 루프 데드락 방지
@router.get("/background/status")
async def get_background_status(): 
    """백그라운드 태스크 상태 및 진행률 반환"""
    ctx = _get_ctx()

    fg = getattr(ctx, "foreground_scheduler", None)
    foreground_info = {
        "active_count": fg.active_count if fg else 0,
        "is_blocking_background": fg.is_active if fg else False,
    }

    # TimeDispatcher 티켓 발행 현황
    latest_trading_date = await ctx._mcs.get_latest_trading_date() if ctx._mcs else None
    td = getattr(ctx, "time_dispatcher", None)
    time_dispatcher_info = None
    if td is not None:
        try:
            td_status = td.get_status()
            if isinstance(td_status, dict):
                last_date = td_status.get("last_dispatched_date")
                ticket_issued = isinstance(last_date, str) and last_date == latest_trading_date
                time_dispatcher_info = {
                    "last_dispatched_date": last_date,
                    "last_dispatched_at": td_status.get("last_dispatched_at"),
                    "market_is_open": td_status.get("market_is_open"),
                    "latest_trading_date": latest_trading_date,
                    "ticket_issued_today": ticket_issued,
                    "registered_tasks": td_status.get("registered_tasks", []),
                }
        except Exception:
            pass

    if not ctx.background_scheduler:
        return {"success": True, "foreground": foreground_info, "time_dispatcher": time_dispatcher_info, "data": []}

    result = []
    for item in ctx.background_scheduler.get_all_status():
        name = item["name"]
        task = ctx.background_scheduler.get_task(name)
        
        schedule_type = "unknown"
        if task:
            module_name = task.__class__.__module__
            if "strategy_scheduler" in module_name:
                schedule_type = "intraday"
            elif "after_market" in module_name:
                schedule_type = "after_market"
            elif "intraday" in module_name:
                schedule_type = "intraday"
            elif "always_on" in module_name:
                schedule_type = "always_on"

        # 2. Enum 객체 안전성 보장 (명시적 문자열 변환)
        raw_state = item.get("state")
        state_str = raw_state.value if hasattr(raw_state, "value") else str(raw_state)

        # 3. IDLE 상태일 경우 방어 로직: 대부분 태스크는 get_progress() 호출을 생략하지만,
        #    강제 수집 API로 직접 실행되는 태스크(예: force_run 즉시 실행)가 내부 플래그
        #    를 통해 진행 중임을 알릴 수 있으므로 그런 경우에는 get_progress()를 호출하여
        #    실제 진행 상태를 반영하도록 합니다.
        progress = None
        if task:
            if state_str == "idle" and name != "strategy_scheduler":
                # If the task exposes an internal progress or running flag, call get_progress()
                if getattr(task, "_is_refreshing", False) or hasattr(task, "_progress"):
                    try:
                        progress = task.get_progress()
                    except Exception as e:
                        progress = {"running": False, "error": str(e)}
                else:
                    # Default safe placeholder for not-yet-started tasks
                    progress = {"running": False, "status": "Waiting to start"}
            else:
                try:
                    progress = task.get_progress()
                except Exception as e:
                    progress = {"running": False, "error": str(e)}

        result.append({
            "name": name,
            "state": state_str,
            "priority": item.get("priority"),
            "schedule_type": schedule_type,
            "schedule_order": _SCHEDULE_ORDER.get(schedule_type, 99),
            "progress": progress,
        })

    # 스케줄 유형(실시간 -> 장중 -> 장마감) 순서로 정렬
    result.sort(key=lambda x: x["schedule_order"])
    return {"success": True, "foreground": foreground_info, "time_dispatcher": time_dispatcher_info, "data": result}


# ── 장 마감 후 태스크 강제 수집 엔드포인트 ─────────────────────────────

@router.post("/background/ranking/force-update")
async def force_ranking_update():
    """skip 조건을 무시하고 투자자 랭킹을 강제 재수집한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "ranking_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="RankingTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "투자자 랭킹 강제 수집이 시작되었습니다."}


@router.post("/background/daily-price/force-update")
async def force_daily_price_update():
    """skip 조건을 무시하고 전 종목 현재가를 강제 재수집한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "daily_price_collector_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="DailyPriceCollectorTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run(force_fresh=True))
    return {"success": True, "message": "현재가 강제 수집이 시작되었습니다."}


@router.get("/subscriptions/status")
def get_subscription_status():
    """실시간 현재가 구독 현황 반환 (우선순위별 종목 + 마지막 수신 시각)"""
    ctx = _get_ctx()
    svc = getattr(ctx, "price_subscription_service", None)
    if not svc:
        return {"success": True, "data": None}

    status = svc.get_status()

    # 수정 포인트: active_codes_price와 active_codes_pt를 병합하여 active_set 구성
    streaming_svc = getattr(ctx, "streaming_service", None)
    active_set = set(status.get("active_codes_price", [])) | set(status.get("active_codes_pt", []))

    def _enrich(codes: list) -> list:
        result = []
        for code in codes:
            name = ctx.stock_code_repository.get_name_by_code(code) or code
            price_info = streaming_svc.get_cached_realtime_price(code) if streaming_svc else None
            received_at = None
            price = None
            if isinstance(price_info, dict):
                received_at = price_info.get("received_at")
                price = price_info.get("price")
            result.append({
                "code": code,
                "name": name,
                "active": code in active_set, # 병합된 active_set을 통해 활성화 여부 확인
                "received_at": received_at,
                "price": price,
            })
        return result

    # API 반환 데이터 구성
    return {
        "success": True,
        "data": {
            "active_count": status.get("active_count", 0),
            "max_subscriptions": status.get("max_subscriptions", 40),
            "active_codes_price": status.get("active_codes_price", []),
            "active_codes_pt": status.get("active_codes_pt", []),
            "pending_count": status.get("pending_count", 0),
            "pending_by_priority": {
                "CRITICAL": _enrich(status.get("pending_by_priority", {}).get("CRITICAL", [])),
                "HIGH": _enrich(status.get("pending_by_priority", {}).get("HIGH", [])),
                "MEDIUM": _enrich(status.get("pending_by_priority", {}).get("MEDIUM", [])),
                "LOW": _enrich(status.get("pending_by_priority", {}).get("LOW", [])),
            }
        }
    }


@router.post("/background/watchlist/force-update")
async def force_watchlist_update():
    """skip 조건을 무시하고 전일 기준 우량주를 강제 재생성한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "premium_watchlist_generator_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="PremiumWatchlistGeneratorTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 생성이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "전일기준우량주 강제 생성이 시작되었습니다."}


@router.post("/background/cache-warmup/force-update")
async def force_cache_warmup():
    """skip 조건을 무시하고 캐시 웜업을 강제 실행한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "cache_warmup_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="CacheWarmupTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 웜업이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "캐시 웜업이 시작되었습니다."}


@router.post("/background/newhigh/force-update")
async def force_newhigh_update():
    """skip 조건을 무시하고 52주 신고가 탐색을 강제 실행한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "newhigh_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="NewHighTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 탐색이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "52주 신고가 강제 탐색이 시작되었습니다."}


@router.post("/background/minervini/force-update")
async def force_minervini_update():
    """skip 조건을 무시하고 Minervini Stage2 캐시를 강제 갱신한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "minervini_update_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="MinerviniUpdateTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_run())
    return {"success": True, "message": "Minervini S2 강제 갱신이 시작되었습니다."}
