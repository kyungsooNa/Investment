"""
유튜브 일일 다이제스트 API 엔드포인트 (youtube.html).

채널은 사용자가 URL/핸들로 등록한다. channel_id 해석은 서버가 맡는다 —
사용자가 `UC...` 24자를 직접 알아낼 방법이 없다.
"""
import logging

from fastapi import APIRouter, Body, HTTPException

from view.web.api_common import _get_ctx

router = APIRouter(prefix="/youtube", tags=["youtube"])
logger = logging.getLogger(__name__)


def _ctx_attr(ctx, name: str, default=None):
    """MagicMock 테스트 컨텍스트에서 미정의 속성이 truthy Mock으로 생기는 것을 피한다."""
    if hasattr(ctx, "__dict__"):
        return ctx.__dict__.get(name, default)
    return getattr(ctx, name, default)


# --- 채널 관리 ---------------------------------------------------------------


@router.get("/channels")
async def list_channels():
    """등록된 채널 목록 (비활성 포함)."""
    ctx = _get_ctx()
    return await ctx.youtube_channel_repository.get_all()


@router.post("/channels")
async def add_channel(payload: dict = Body(...)):
    """채널 URL/핸들을 channel_id 로 해석해 등록한다."""
    url = str((payload or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="채널 URL 또는 핸들을 입력하세요.")

    ctx = _get_ctx()
    resolved = await ctx.youtube_transcript_collector.resolve_channel(url)
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail=f"채널을 찾을 수 없습니다: {url}",
        )

    added = await ctx.youtube_channel_repository.add(
        resolved["channel_id"],
        handle=resolved.get("handle", ""),
        title=resolved.get("title", ""),
    )
    return {
        "added": added,
        "channel_id": resolved["channel_id"],
        "handle": resolved.get("handle", ""),
        "title": resolved.get("title", ""),
    }


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    ctx = _get_ctx()
    if not await ctx.youtube_channel_repository.remove(channel_id):
        raise HTTPException(status_code=404, detail=f"등록되지 않은 채널: {channel_id}")
    return {"removed": True, "channel_id": channel_id}


@router.patch("/channels/{channel_id}")
async def toggle_channel(channel_id: str, payload: dict = Body(...)):
    """수집 on/off 전환."""
    enabled = bool((payload or {}).get("enabled", True))
    ctx = _get_ctx()
    if not await ctx.youtube_channel_repository.set_enabled(channel_id, enabled):
        raise HTTPException(status_code=404, detail=f"등록되지 않은 채널: {channel_id}")
    return {"channel_id": channel_id, "enabled": enabled}


# --- 리포트 조회 -------------------------------------------------------------


@router.get("/reports")
async def list_reports(limit: int = 30):
    """리포트 날짜 목록 (본문 제외)."""
    ctx = _get_ctx()
    return await ctx.youtube_digest_repository.list_recent(limit=limit)


@router.get("/reports/latest")
async def get_latest_report():
    """가장 최근 리포트. 아직 없으면 report=null.

    bare null 로 돌려주면 프런트 공용 readJsonResponse 가 비객체를 오류로 처리해
    첫 실행 전 화면에 "불러오지 못했습니다"가 뜬다. 그래서 객체로 감싼다.
    """
    ctx = _get_ctx()
    return {"report": await ctx.youtube_digest_repository.latest()}


@router.get("/reports/{report_date}")
async def get_report(report_date: str):
    ctx = _get_ctx()
    report = await ctx.youtube_digest_repository.get(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"리포트 없음: {report_date}")
    return report


# --- 수동 실행 / 상태 ---------------------------------------------------------


@router.post("/run")
async def run_now():
    """스케줄을 기다리지 않고 즉시 1회 생성한다 (AI 한도를 소비한다)."""
    ctx = _get_ctx()
    task = _ctx_attr(ctx, "youtube_digest_task")
    if task is None:
        raise HTTPException(
            status_code=503,
            detail="AI 분석이 비활성화되어 다이제스트를 생성할 수 없습니다.",
        )
    await task.run_once()
    return {"ok": True, "progress": task.get_progress()}


@router.get("/status")
async def get_status():
    ctx = _get_ctx()
    task = _ctx_attr(ctx, "youtube_digest_task")
    if task is None:
        return {"enabled": False, "progress": None}
    return {"enabled": True, "progress": task.get_progress()}
