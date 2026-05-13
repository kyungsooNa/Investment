"""운영자 대시보드 API.

Kill Switch / Risk Gate 차단 상태와 최근 전이 이력을 한 곳에서 조회한다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from view.web.api_common import _get_ctx

router = APIRouter()


@router.get("/operator/status")
async def get_operator_status():
    """현재 active 차단 목록과 Kill Switch 상태 요약.

    Returns:
        {
          "active_alerts": [...],
          "kill_switch": {is_tripped, trip_reason, ...},
          "summary": {total_active, has_critical, has_kill_switch}
        }
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "operator_alert_service", None)
    ks = getattr(ctx, "kill_switch_service", None)

    active_alerts = svc.get_active_alerts() if svc else []
    ks_status = ks.get_status() if ks else {}

    has_critical = any(
        a.get("severity") in ("critical", "error") for a in active_alerts
    )
    has_kill_switch = bool(ks_status.get("is_tripped") or ks_status.get("strategy_tripped"))

    return {
        "active_alerts": active_alerts,
        "kill_switch": ks_status,
        "summary": {
            "total_active": len(active_alerts),
            "has_critical": has_critical,
            "has_kill_switch": has_kill_switch,
        },
    }


@router.get("/operator/alerts")
async def get_operator_alerts(limit: int = 100, source: str = "", since: str = ""):
    """운영자 알림 전이 이력 조회.

    Args:
        limit:  최대 반환 건수 (기본 100).
        source: AlertSource 필터 (KILL_SWITCH / RISK_GATE). 빈 문자열이면 전체.
        since:  ISO 타임스탬프. 이 시각 이후 항목만 반환.

    Returns:
        {"alerts": [...], "total": int}
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "operator_alert_service", None)

    history = svc.get_history(limit=limit) if svc else []

    if source:
        history = [h for h in history if h.get("source") == source]
    if since:
        history = [h for h in history if h.get("timestamp", "") >= since]

    return {"alerts": history, "total": len(history)}


@router.post("/operator/alerts/{dedup_key:path}/resolve")
async def resolve_operator_alert(dedup_key: str, reason: str = ""):
    """운영자 수동 해제.

    Args:
        dedup_key: URL-encoded dedup key (e.g. kill_switch:global).
        reason:    해제 사유 (query param 또는 body).

    Returns:
        {"resolved": bool, "dedup_key": str}
    """
    ctx = _get_ctx()
    svc = getattr(ctx, "operator_alert_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="operator_alert_service not initialized")

    # dedup_key에서 source 추출 시도
    from common.operator_alert_types import AlertSource
    if dedup_key.startswith("kill_switch:"):
        source = AlertSource.KILL_SWITCH
    else:
        source = AlertSource.RISK_GATE

    resolved = await svc.resolve(source, dedup_key, reason or "운영자 수동 해제")
    return {"resolved": resolved, "dedup_key": dedup_key}
