"""
시스템 상태 및 캐시 모니터링 API 엔드포인트.
"""
import asyncio
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx

router = APIRouter()

# 태스크별 실행 스케줄 유형
# intraday: 장 중에만 의미있는 태스크 (장 마감 후 비활성)
# after_market: 장 마감 후 실행되는 배치 태스크 (장 중 비활성)
# realtime: 항상 동작해야 하는 실시간 태스크
_SCHEDULE_TYPES = {
    "websocket_watchdog":  "intraday",
    "strategy_scheduler":  "intraday",
    "ranking_refresh":     "after_market",
    "daily_price_collector": "after_market",
    "ohlcv_update":        "after_market",
    "전일기준주도주_생성":  "after_market",
}

_SCHEDULE_ORDER = {
    "realtime": 0,
    "intraday": 1,
    "after_market": 2,
    "unknown": 99,
}


@router.get("/cache/status")
async def get_cache_status(expand: bool = True):
    """메모리 캐시 상태 및 적중률 통계 반환"""
    ctx = _get_ctx()
    latest_trading_date = await ctx._mcs.get_latest_trading_date() if ctx._mcs else None
    stats = ctx.get_cache_stats(expand=expand, latest_trading_date=latest_trading_date) or {}

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


@router.get("/background/status")
def get_background_status():
    """백그라운드 태스크 상태 및 진행률 반환"""
    ctx = _get_ctx()
    if not ctx.background_scheduler:
        return {"success": True, "data": []}

    result = []
    for item in ctx.background_scheduler.get_all_status():
        name = item["name"]
        task = ctx.background_scheduler.get_task(name)
        schedule_type = _SCHEDULE_TYPES.get(name, "unknown")
        
        # --- 모듈(파일) 경로를 기반으로 스케줄 유형 동적 판별 ---
        schedule_type = "unknown"
        if task:
            module_name = task.__class__.__module__  # 예: "task.background.after_market.ranking_task"
            
            # 특정 태스크명 우선 확인 (폴더 경로에 포함된 이름으로 인해 잘못 분류되는 것을 방지)
            if "strategy_scheduler" in module_name:
                schedule_type = "intraday"
            elif "after_market" in module_name:
                schedule_type = "after_market"
            elif "intraday" in module_name:
                schedule_type = "intraday"
            elif "realtime" in module_name:
                schedule_type = "realtime"

        result.append({
            "name": name,
            "state": item["state"],
            "priority": item["priority"],
            "schedule_type": schedule_type,
            "schedule_order": _SCHEDULE_ORDER.get(schedule_type, 99),
            "progress": task.get_progress() if task else None,
        })

    # 스케줄 유형(실시간 -> 장중 -> 장마감) 순서로 정렬
    result.sort(key=lambda x: x["schedule_order"])
    return {"success": True, "data": result}


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

    asyncio.create_task(task.force_collect())
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

    asyncio.create_task(task.force_collect())
    return {"success": True, "message": "현재가 강제 수집이 시작되었습니다."}


@router.get("/subscriptions/status")
def get_subscription_status():
    """실시간 현재가 구독 현황 반환 (우선순위별 종목 + 마지막 수신 시각)"""
    ctx = _get_ctx()
    svc = getattr(ctx, "subscription_service", None)
    if not svc:
        return {"success": True, "data": None}

    status = svc.get_status()

    # 우선순위별 종목 목록 (active 여부 + 이름 + 구독 시작 시각 + 마지막 수신 시각 부가)
    streaming_svc = getattr(ctx, "streaming_service", None)
    active_set = set(status.get("active_price_codes", status.get("active_codes", [])))
    subscribed_at_map: dict = status.get("subscribed_at", {})

    def _enrich(codes: list) -> list:
        result = []
        for code in codes:
            name = ctx.stock_code_repository.get_name_by_code(code) or code
            price_info = streaming_svc.get_cached_realtime_price(code) if streaming_svc else None
            received_at = None
            if isinstance(price_info, dict):
                received_at = price_info.get("received_at")
            result.append({
                "code": code,
                "name": name,
                "active": code in active_set,
                "subscribed_at": subscribed_at_map.get(code),
                "received_at": received_at,
            })
        return result

    by_priority = status.get("pending_by_priority", {})
    return {
        "success": True,
        "data": {
            "active_count": status.get("active_subs_count", 0),
            "max_subscriptions": status["max_subscriptions"],
            "pending_count": status["pending_count"],
            "CRITICAL": _enrich(by_priority.get("CRITICAL", [])),
            "HIGH":     _enrich(by_priority.get("HIGH", [])),
            "MEDIUM":   _enrich(by_priority.get("MEDIUM", [])),
            "LOW":      _enrich(by_priority.get("LOW", [])),
        },
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

    asyncio.create_task(task.force_generate())
    return {"success": True, "message": "전일기준우량주 강제 생성이 시작되었습니다."}
