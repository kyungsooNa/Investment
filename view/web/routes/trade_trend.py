"""
수출입동향 조회 API.
"""
from fastapi import APIRouter, Query

from services.trade_trend_service import NationalTradeTrendRelease
from view.web.api_common import _get_ctx

router = APIRouter()


def _release_to_dict(release: NationalTradeTrendRelease, source_type: str) -> dict:
    return {
        "source": release.source,
        "source_type": source_type,
        "phase": release.phase,
        "title": release.title,
        "url": release.url,
        "period_label": release.period_label,
        "export_amount_100m_usd": release.export_amount_100m_usd,
        "export_yoy_pct": release.export_yoy_pct,
        "import_amount_100m_usd": release.import_amount_100m_usd,
        "import_yoy_pct": release.import_yoy_pct,
        "trade_balance_100m_usd": release.trade_balance_100m_usd,
        "trade_balance_label": release.trade_balance_label,
        "published_at": release.published_at,
        "highlights": list(release.highlights or []),
        "sent_at": "",
        "dedup_key": release.dedup_key,
    }


def _merge_rows(*groups: list[dict]) -> list[dict]:
    rows_by_key: dict[str, dict] = {}
    for rows in groups:
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("dedup_key") or row.get("url") or "")
            if not key:
                continue
            current = rows_by_key.get(key, {})
            merged = dict(row)
            if current:
                merged = {**row, **{k: v for k, v in current.items() if v not in (None, "", [])}}
                if current.get("source_type") == "sent":
                    merged["source_type"] = "sent"
            rows_by_key[key] = merged
    return sorted(
        rows_by_key.values(),
        key=lambda item: (
            str(item.get("published_at") or ""),
            str(item.get("period_label") or ""),
            str(item.get("sent_at") or ""),
        ),
        reverse=True,
    )


@router.get("/trade-trends/national/history")
async def get_national_trade_trend_history(
    include_recent: bool = Query(True),
    limit: int = Query(60, ge=1, le=300),
):
    """저장된 알림 이력과 공식 페이지 최신 후보를 함께 반환한다."""
    ctx = _get_ctx()
    task = getattr(ctx, "trade_trend_monitor_task", None)
    stored_rows = []
    if task is not None and hasattr(task, "get_national_release_history"):
        stored_rows = task.get_national_release_history()

    recent_rows = []
    client = getattr(ctx, "national_trade_trend_client", None)
    if include_recent and client is not None:
        releases = await client.fetch_recent_releases()
        recent_rows = [_release_to_dict(release, "official") for release in releases]

    rows = _merge_rows(stored_rows, recent_rows)[:limit]
    return {
        "success": True,
        "data": {
            "rows": rows,
            "latest": rows[0] if rows else None,
            "stored_count": len(stored_rows),
            "recent_count": len(recent_rows),
        },
    }
