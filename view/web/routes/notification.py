"""
알림 센터 API 엔드포인트.
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from view.web.api_common import _get_ctx

router = APIRouter()


@router.get("/notifications/recent")
async def get_recent_notifications(
    category: Optional[str] = Query(None),
    count: int = Query(50, ge=1, le=200),
):
    """최근 알림 목록 조회."""
    ctx = _get_ctx()
    items = ctx.notification_manager.get_recent(count=count, category=category)
    return {"notifications": items}


@router.get("/notifications/stream")
async def stream_notifications(request: Request):
    """SSE 스트리밍: 알림 이벤트를 실시간으로 브라우저에 전달."""
    ctx = _get_ctx()
    queue = ctx.notification_manager.create_subscriber_queue()

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
            ctx.notification_manager.remove_subscriber_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
