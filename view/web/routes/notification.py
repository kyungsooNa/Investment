"""
알림 센터 API 엔드포인트.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from view.web.api_common import _get_ctx
from services.notification_service import NotificationCategory

router = APIRouter()
SSE_KEEPALIVE_TIMEOUT_SEC = 15


@router.get("/notifications/recent")
async def get_recent_notifications(
    category: Optional[NotificationCategory] = Query(None),
    count: int = Query(50, ge=1, le=200),
):
    """최근 알림 목록 조회."""
    ctx = _get_ctx()
    items = ctx.notification_service.get_recent(count=count, category=category)
    return {"notifications": items}


@router.get("/notifications/stream")
async def stream_notifications(request: Request):
    """SSE 스트리밍: 알림 이벤트를 실시간으로 브라우저에 전달."""
    ctx = _get_ctx()
    queue = ctx.notification_service.create_subscriber_queue()

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_TIMEOUT_SEC)
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
            ctx.notification_service.remove_subscriber_queue(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
