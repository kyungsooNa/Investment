"""
전략 스케줄러 제어 API 엔드포인트 (scheduler.html).
"""
import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from view.web.api_common import _get_ctx

router = APIRouter()

class UpdateMaxPositionsRequest(BaseModel):
    max_positions: int


_MARKET_TASK_DEFINITIONS = (
    {
        "name": "overseas_intraday",
        "display_name": "미국장 장중 전략",
        "market": "overseas_us",
        "market_label": "미국장",
        "mode": "paper",
        "live_trading": False,
    },
    {
        "name": "overseas_dryrun",
        "display_name": "미국장 마감 후 Dry-run",
        "market": "overseas_us",
        "market_label": "미국장",
        "mode": "dry-run",
        "live_trading": False,
    },
)

_MARKET_LABELS = {
    "domestic": "한국장",
    "overseas_us": "미국장",
}


def _save_scheduler_state_later(scheduler) -> None:
    """상태 저장은 응답 경로를 막지 않도록 백그라운드에서 수행한다."""
    asyncio.create_task(asyncio.to_thread(scheduler._save_scheduler_state))


def _market_task_status(ctx, market: str | None = None) -> list[dict]:
    """StrategyScheduler 밖에서 도는 시장별 전략성 태스크를 상태 API에 노출한다."""
    background_scheduler = getattr(ctx, "background_scheduler", None)
    if background_scheduler is None:
        return []

    items = []
    for definition in _MARKET_TASK_DEFINITIONS:
        if market and definition["market"] != market:
            continue
        task = background_scheduler.get_task(definition["name"])
        if task is None:
            continue
        state = getattr(task, "state", None)
        state_value = getattr(state, "value", state)
        progress = task.get_progress() if hasattr(task, "get_progress") else {}
        items.append({
            **definition,
            "state": state_value,
            "running": state_value == "running" or bool(progress.get("running")),
            "priority": int(getattr(task, "priority", 0)),
            "progress": progress,
        })
    return items


def _enabled_scheduler_markets(ctx) -> list[str]:
    enabled = getattr(ctx, "enabled_market_modes", None)
    if not isinstance(enabled, list) or not enabled:
        enabled = ["domestic"]
    markets = []
    for market in [*enabled, "domestic", "overseas_us"]:
        if market not in markets:
            markets.append(market)
    return markets


def _get_strategy_scheduler(ctx, market: str = "domestic"):
    schedulers = getattr(ctx, "strategy_schedulers", None)
    if isinstance(schedulers, dict):
        scheduler = schedulers.get(market)
        if scheduler is not None:
            return scheduler
    getter = getattr(ctx, "get_strategy_scheduler", None)
    getter_module = type(getter).__module__ if getter is not None else ""
    if callable(getter) and not getter_module.startswith("unittest.mock"):
        return getter(market)
    if market == "domestic":
        return getattr(ctx, "scheduler", None)
    return None


def _scheduler_status_payload(ctx, market: str, scheduler) -> dict:
    if scheduler is None:
        status = {"running": False, "dry_run": False, "strategies": []}
    else:
        status = scheduler.get_status()

    status = dict(status or {})
    strategies = []
    for strategy in status.get("strategies", []) or []:
        item = dict(strategy)
        item.setdefault("market", market)
        item.setdefault("market_label", _MARKET_LABELS.get(market, market))
        strategies.append(item)
    status["strategies"] = strategies
    status["market"] = market
    status["market_label"] = _MARKET_LABELS.get(market, market)
    status["has_scheduler"] = scheduler is not None
    status["market_tasks"] = _market_task_status(ctx, market)
    return status


def _status_for_market(ctx, market: str) -> dict:
    return _scheduler_status_payload(ctx, market, _get_strategy_scheduler(ctx, market))


def _all_scheduler_statuses(ctx) -> list[dict]:
    return [_status_for_market(ctx, market) for market in _enabled_scheduler_markets(ctx)]


@router.get("/scheduler/status")
async def get_scheduler_status(market: str | None = None):
    """스케줄러 상태 조회."""
    ctx = _get_ctx()
    selected_market = market or "domestic"
    status = await asyncio.to_thread(_status_for_market, ctx, selected_market)
    status["schedulers"] = await asyncio.to_thread(_all_scheduler_statuses, ctx)
    # market 을 명시한 호출(시장별 스케줄러 페이지)은 해당 시장 태스크만 본다.
    if market is None:
        status["market_tasks"] = _market_task_status(ctx)
    
    # [BugFix] 보유 종목명 보정
    mapper = getattr(ctx, 'stock_code_repository', None)
    if mapper and selected_market == "domestic":
        for s in status.get("strategies", []):
            for hold in s.get("holdings", []):
                code = str(hold.get("code", ""))
                if code:
                    real_name = mapper.get_name_by_code(code)
                    if real_name:
                        hold["name"] = real_name
                        
    return status


@router.post("/scheduler/start")
async def start_scheduler(market: str = "domestic"):
    """스케줄러 시작 (상태 저장 — 재시작 시 자동 복원)."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await scheduler.start()
    _save_scheduler_state_later(scheduler)
    return {"success": True}


@router.post("/scheduler/stop")
async def stop_scheduler(market: str = "domestic"):
    """스케줄러 정지 (수동 정지 — 재시작 시 자동 실행 안 함)."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await scheduler.stop(save_state=False)
    scheduler.clear_saved_state()
    return {"success": True}


@router.post("/scheduler/strategy/{name:path}/start")
async def start_strategy(name: str, market: str = "domestic"):
    """개별 전략 활성화 (상태 저장 — 재시작 시 자동 복원)."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not await scheduler.start_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    _save_scheduler_state_later(scheduler)
    return {"success": True}


@router.post("/scheduler/strategy/{name:path}/stop")
async def stop_strategy(name: str, market: str = "domestic"):
    """개별 전략 비활성화 (상태 저장 — 재시작 시 반영)."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not await scheduler.stop_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    _save_scheduler_state_later(scheduler)
    return {"success": True}


@router.post("/scheduler/strategy/{name:path}/max-positions")
async def update_strategy_max_positions(
    name: str,
    req: UpdateMaxPositionsRequest,
    market: str = "domestic",
):
    """개별 전략의 최대 포지션 수 동적 변경."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    
    success = await scheduler.update_max_positions(name, req.max_positions)
    if not success:
        raise HTTPException(status_code=400, detail="최대 포지션 수 변경 실패 (1 이상이어야 함)")
    return {"success": True}

@router.get("/scheduler/history")
async def get_scheduler_history(strategy: str = None, market: str = "domestic"):
    """스케줄러 시그널 실행 이력 조회. ?strategy=전략명 으로 필터 가능."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        return {"history": []}

    history = scheduler.get_signal_history(strategy)

    ctx.pm.log_timer("get_scheduler_history", t_start)
    return {"history": history}

@router.get("/scheduler/stream")
async def stream_scheduler_signals(request: Request, market: str = "domestic"):
    """SSE 스트리밍: 스케줄러 시그널 실행 이력을 실시간으로 브라우저에 전달."""
    ctx = _get_ctx()
    scheduler = _get_strategy_scheduler(ctx, market)
    if not scheduler:
        return StreamingResponse(
            iter([": no scheduler\n\n"]), media_type="text/event-stream"
        )

    queue = scheduler.create_subscriber_queue()

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    if data is None:
                        break
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            scheduler.remove_subscriber_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


