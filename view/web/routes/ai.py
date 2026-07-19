"""
AI 공통 API 엔드포인트 — 일일 사용량 조회.

종목 분석/뉴스 검토/랭킹 분석이 같은 인터랙티브 한도를 나눠 쓰므로,
버튼을 누르기 전에 남은 요청 수를 볼 수 있어야 한다.
"""
from fastapi import APIRouter

from view.web.api_common import _get_ctx

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/usage")
async def get_ai_usage():
    """오늘의 AI 요청 사용량 스냅샷을 반환한다(usage_type 별 내역 포함)."""
    ctx = _get_ctx()
    limiter = getattr(ctx, "ai_usage_limiter", None)
    if limiter is None:
        return {
            "rt_cd": "0",
            "msg1": "AI 분석이 비활성화되어 있습니다.",
            "data": {"enabled": False},
        }
    try:
        snapshot = await limiter.get_snapshot()
    except Exception as exc:
        ctx.logger.warning(f"[ai-usage] 사용량 조회 실패: {type(exc).__name__}: {exc}")
        return {"rt_cd": "1", "msg1": "사용량 조회에 실패했습니다.", "data": None}
    return {"rt_cd": "0", "msg1": "성공", "data": snapshot}
