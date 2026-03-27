"""
실시간 현재가 구독 관리 API 엔드포인트.
RealtimeSubscriptionService를 통해 UI에서 구독 요청/해지/현황 조회.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from view.web.api_common import _get_ctx
from services.realtime_subscription_service import SubscriptionPriority

router = APIRouter()


class SubscribeRequest(BaseModel):
    code: str
    reason: str  # "ui_view", "watchlist" 등


@router.post("/streaming/subscribe")
async def subscribe_stock(req: SubscribeRequest):
    """UI에서 특정 종목 실시간 가격 구독 요청."""
    ctx = _get_ctx()
    svc = getattr(ctx, "subscription_service", None)
    if not svc:
        raise HTTPException(status_code=503, detail="RealtimeSubscriptionService가 초기화되지 않았습니다")

    category_key = f"ui_{req.reason}"
    await svc.add_subscription(req.code, SubscriptionPriority.LOW, category_key)
    return {"success": True, "code": req.code, "category": category_key}


@router.post("/streaming/unsubscribe")
async def unsubscribe_stock(req: SubscribeRequest):
    """UI에서 특정 종목 실시간 가격 구독 해지."""
    ctx = _get_ctx()
    svc = getattr(ctx, "subscription_service", None)
    if not svc:
        raise HTTPException(status_code=503, detail="RealtimeSubscriptionService가 초기화되지 않았습니다")

    category_key = f"ui_{req.reason}"
    await svc.remove_subscription(req.code, category_key)
    return {"success": True, "code": req.code, "category": category_key}


@router.get("/streaming/status")
def get_streaming_status():
    """현재 실시간 구독 현황 반환."""
    ctx = _get_ctx()
    svc = getattr(ctx, "subscription_service", None)
    if not svc:
        return {"success": True, "data": {"active_count": 0, "active_codes": []}}

    return {"success": True, "data": svc.get_status()}
