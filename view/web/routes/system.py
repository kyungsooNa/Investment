"""
시스템 상태 및 캐시 모니터링 API 엔드포인트.
"""
from fastapi import APIRouter
from view.web.api_common import _get_ctx

router = APIRouter()

@router.get("/cache/status")
def get_cache_status(expand: bool = True):
    """메모리 캐시 상태 및 적중률 통계 반환"""
    ctx = _get_ctx()
    stats = ctx.get_cache_stats(expand=expand) or {}
    
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