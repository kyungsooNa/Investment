"""
전략 스케줄러 제어 API 엔드포인트 (scheduler.html).
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from view.web.api_common import _get_ctx

router = APIRouter()


@router.get("/scheduler/status")
async def get_scheduler_status():
    """스케줄러 상태 조회."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        return {"running": False, "strategies": []}
    return ctx.scheduler.get_status()


@router.post("/scheduler/start")
async def start_scheduler():
    """스케줄러 시작 (상태 저장 — 재시작 시 자동 복원)."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await ctx.scheduler.start()
    ctx.scheduler._save_scheduler_state()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/stop")
async def stop_scheduler():
    """스케줄러 정지 (수동 정지 — 재시작 시 자동 실행 안 함)."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    await ctx.scheduler.stop(save_state=False)
    ctx.scheduler.clear_saved_state()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/strategy/{name:path}/start")
async def start_strategy(name: str):
    """개별 전략 활성화 (상태 저장 — 재시작 시 자동 복원)."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not await ctx.scheduler.start_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    ctx.scheduler._save_scheduler_state()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.post("/scheduler/strategy/{name:path}/stop")
async def stop_strategy(name: str):
    """개별 전략 비활성화 (상태 저장 — 재시작 시 반영)."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        raise HTTPException(status_code=503, detail="스케줄러가 초기화되지 않았습니다")
    if not ctx.scheduler.stop_strategy(name):
        raise HTTPException(status_code=404, detail=f"전략 '{name}'을 찾을 수 없습니다")
    ctx.scheduler._save_scheduler_state()
    return {"success": True, "status": ctx.scheduler.get_status()}


@router.get("/scheduler/history")
async def get_scheduler_history(strategy: str = None):
    """스케줄러 시그널 실행 이력 조회. ?strategy=전략명 으로 필터 가능."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    if not ctx.scheduler:
        return {"history": []}

    history = ctx.scheduler.get_signal_history(strategy)

    # [BugFix] 종목명 보정: 스케줄러 이력에 저장된 이름이 부정확할 수 있으므로 Mapper를 통해 최신 종목명으로 덮어씀
    mapper = getattr(ctx, 'stock_code_mapper', None)
    if mapper:
        for item in history:
            code = str(item.get('code', ''))
            if code:
                real_name = mapper.get_name_by_code(code)
                if real_name:
                    item['name'] = real_name

    ctx.pm.log_timer("get_scheduler_history", t_start)
    return {"history": history}

@router.get("/scheduler/stream")
async def stream_scheduler_signals(request: Request):
    """SSE 스트리밍: 스케줄러 시그널 실행 이력을 실시간으로 브라우저에 전달."""
    ctx = _get_ctx()
    if not ctx.scheduler:
        return StreamingResponse(
            iter([": no scheduler\n\n"]), media_type="text/event-stream"
        )

    queue = ctx.scheduler.create_subscriber_queue()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    if data is None:
                        break
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            ctx.scheduler.remove_subscriber_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/scheduler/strategy/오닐스퀴즈돌파/generate-pool-a")
async def generate_osb_pool_a():
    """오닐 스퀴즈 전략 Pool A 생성 (장 마감 후 수동 실행)."""
    ctx = _get_ctx()
    t_start = ctx.pm.start_timer()
    if not ctx.initialized:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    if not hasattr(ctx, "oneil_universe_service") or not ctx.oneil_universe_service:
        raise HTTPException(status_code=404, detail="오닐 유니버스 서비스가 초기화되지 않았습니다.")
    result = await ctx.oneil_universe_service.generate_pool_a()
    ctx.pm.log_timer("generate_osb_pool_a", t_start)
    return {"success": True, "result": result}