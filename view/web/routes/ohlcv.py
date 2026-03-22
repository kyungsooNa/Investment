"""
OHLCV 수집 제어 API 엔드포인트.
- GET  /ohlcv/progress       : 현재 수집 진행률 조회
- POST /ohlcv/force-update   : skip 조건 무시 강제 전체 수집 트리거
"""
import asyncio
from fastapi import APIRouter, HTTPException
from view.web.api_common import _get_ctx

router = APIRouter()


@router.get("/ohlcv/progress")
async def get_ohlcv_progress():
    """OHLCV 수집 진행률 반환."""
    ctx = _get_ctx()
    task = getattr(ctx, "ohlcv_update_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="OhlcvUpdateTask가 초기화되지 않았습니다")
    return task.get_progress()


@router.post("/ohlcv/force-update")
async def force_ohlcv_update():
    """skip 조건을 무시하고 전 종목 OHLCV를 강제 재수집한다.

    - 최초 설치 / 다른 머신 이전 후 전체 백필이 필요할 때
    - DB 데이터 정합성이 의심될 때
    수집은 백그라운드로 실행되며 /api/ohlcv/progress 로 진행률을 확인할 수 있다.
    """
    ctx = _get_ctx()
    task = getattr(ctx, "ohlcv_update_task", None)
    if not task:
        raise HTTPException(status_code=503, detail="OhlcvUpdateTask가 초기화되지 않았습니다")

    progress = task.get_progress()
    if progress.get("running"):
        raise HTTPException(status_code=409, detail="이미 수집이 진행 중입니다")

    asyncio.create_task(task.force_collect())
    return {"success": True, "message": "강제 수집이 시작되었습니다. /api/ohlcv/progress 에서 진행률을 확인하세요."}
