"""
계좌 보호용 Kill Switch API.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from view.web.api_common import _get_ctx, check_auth

router = APIRouter()


class TripRequest(BaseModel):
    reason: str = "수동 트립"


@router.get("/kill-switch/status")
async def get_kill_switch_status(request: Request):
    """Kill Switch 현재 상태 반환."""
    check_auth(request)
    ctx = _get_ctx()
    if not ctx.kill_switch_service:
        raise HTTPException(status_code=503, detail="Kill Switch 서비스가 초기화되지 않았습니다.")
    return ctx.kill_switch_service.get_status()


@router.post("/kill-switch/trip")
async def trip_kill_switch(req: TripRequest, request: Request):
    """Kill Switch 수동 트립. 모든 주문·전략이 즉시 차단됩니다."""
    check_auth(request)
    ctx = _get_ctx()
    if not ctx.kill_switch_service:
        raise HTTPException(status_code=503, detail="Kill Switch 서비스가 초기화되지 않았습니다.")
    operator = request.cookies.get("access_token", "unknown")
    await ctx.kill_switch_service.manual_trip(req.reason, operator)
    return {"ok": True, "message": f"Kill Switch 트립 완료: {req.reason}"}


@router.post("/kill-switch/reset")
async def reset_kill_switch(request: Request):
    """Kill Switch 수동 해제. 운영자 확인 후 주문·전략이 재개됩니다."""
    check_auth(request)
    ctx = _get_ctx()
    if not ctx.kill_switch_service:
        raise HTTPException(status_code=503, detail="Kill Switch 서비스가 초기화되지 않았습니다.")
    operator = request.cookies.get("access_token", "unknown")
    await ctx.kill_switch_service.manual_reset(operator)
    return {"ok": True, "message": "Kill Switch 해제 완료"}
