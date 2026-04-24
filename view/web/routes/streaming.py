"""
실시간 현재가 구독 관리 API 엔드포인트.
PriceSubscriptionService를 통해 UI에서 구독 요청/해지/현황 조회.
"""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from repositories.streaming_stock_repo import StreamingType
from view.web.api_common import _get_ctx
from services.price_subscription_service import SubscriptionPriority

router = APIRouter()
SSE_KEEPALIVE_TIMEOUT_SEC = 15


class SubscribeRequest(BaseModel):
    code: str
    reason: str  # "ui_view", "watchlist" 등


@router.post("/streaming/subscribe")
async def subscribe_stock(req: SubscribeRequest):
    """UI에서 특정 종목 실시간 가격 구독 요청."""
    ctx = _get_ctx()
    svc = getattr(ctx, "price_subscription_service", None)
    if not svc:
        raise HTTPException(status_code=503, detail="PriceSubscriptionService가 초기화되지 않았습니다")

    category_key = f"ui_{req.reason}"
    await svc.add_subscription(req.code, SubscriptionPriority.LOW, category_key, StreamingType.UNIFIED_PRICE)
    return {"success": True, "code": req.code, "category": category_key, "message": f"{req.code} 종목이 실시간 가격 구독 대상에 추가되었습니다."}


@router.post("/streaming/unsubscribe")
async def unsubscribe_stock(req: SubscribeRequest):
    """UI에서 특정 종목 실시간 가격 구독 해지."""
    ctx = _get_ctx()
    svc = getattr(ctx, "price_subscription_service", None)
    if not svc:
        raise HTTPException(status_code=503, detail="PriceSubscriptionService가 초기화되지 않았습니다")

    category_key = f"ui_{req.reason}"
    await svc.remove_subscription(req.code, category_key)
    return {"success": True, "code": req.code, "category": category_key}


@router.get("/streaming/status")
def get_streaming_status():
    """현재 실시간 구독 현황 반환."""
    ctx = _get_ctx()
    svc = getattr(ctx, "price_subscription_service", None)
    if not svc:
        return {"success": True, "data": {"active_count": 0, "active_codes": []}}

    return {"success": True, "data": svc.get_status()}


@router.get("/streaming/price/{code}")
async def stream_stock_price(code: str, request: Request):
    """SSE: 특정 종목 실시간 체결가를 브라우저로 스트리밍."""
    ctx = _get_ctx()
    stream_svc = getattr(ctx, "price_stream_service", None)
    sub_svc = getattr(ctx, "price_subscription_service", None)

    if not stream_svc:
        raise HTTPException(status_code=503, detail="PriceStreamService가 초기화되지 않았습니다")

    queue = stream_svc.create_subscriber_queue(code)
    category = f"sse_ui_{id(queue)}"
    if sub_svc:
        await sub_svc.add_subscription(code, SubscriptionPriority.LOW, category, StreamingType.UNIFIED_PRICE)

    async def event_generator():
        try:
            while True:
                get_task = asyncio.ensure_future(queue.get())
                _, pending = await asyncio.wait({get_task}, timeout=SSE_KEEPALIVE_TIMEOUT_SEC)
                if pending:
                    get_task.cancel()
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
                else:
                    tick = get_task.result()
                    yield f"data: {json.dumps(tick)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            stream_svc.remove_subscriber_queue(code, queue)
            if sub_svc:
                await sub_svc.remove_subscription(code, category)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
