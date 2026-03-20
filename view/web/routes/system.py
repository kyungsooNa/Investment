"""
시스템 상태 및 캐시 모니터링 API 엔드포인트.
"""
from fastapi import APIRouter
from view.web.api_common import _get_ctx

router = APIRouter()

@router.get("/cache/status")
def get_cache_status():
    """메모리 캐시 상태 및 적중률 통계 반환"""
    ctx = _get_ctx()
    stats = ctx.get_cache_stats()
    return {
        "success": True,
        "data": stats
    }