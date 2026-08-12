"""
수출입동향 조회 API.
"""
from datetime import datetime

from fastapi import APIRouter, Query

from repositories.trade_trend_repository import TradeTrendRepository
from services.trade_trend_service import (
    NationalTradeTrendRelease,
    NationalTradeTrendWebClient,
)
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
        "export_mom_change_100m_usd": release.export_mom_change_100m_usd,
        "export_mom_pct": release.export_mom_pct,
        "export_daily_avg_100m_usd": release.export_daily_avg_100m_usd,
        "export_daily_avg_mom_pct": release.export_daily_avg_mom_pct,
        "import_amount_100m_usd": release.import_amount_100m_usd,
        "import_yoy_pct": release.import_yoy_pct,
        "import_mom_change_100m_usd": release.import_mom_change_100m_usd,
        "import_mom_pct": release.import_mom_pct,
        "trade_balance_100m_usd": release.trade_balance_100m_usd,
        "trade_balance_label": release.trade_balance_label,
        "trade_balance_mom_change_100m_usd": release.trade_balance_mom_change_100m_usd,
        "semiconductor_export_amount_100m_usd": release.semiconductor_export_amount_100m_usd,
        "semiconductor_yoy_pct": release.semiconductor_yoy_pct,
        "semiconductor_mom_change_100m_usd": release.semiconductor_mom_change_100m_usd,
        "semiconductor_mom_pct": release.semiconductor_mom_pct,
        "semiconductor_daily_avg_100m_usd": release.semiconductor_daily_avg_100m_usd,
        "semiconductor_daily_avg_mom_pct": release.semiconductor_daily_avg_mom_pct,
        "working_days_current": release.working_days_current,
        "working_days_previous_year": release.working_days_previous_year,
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


def _config_section(ctx, section_name: str):
    full_config = getattr(ctx, "full_config", {}) or {}
    if isinstance(full_config, dict):
        return full_config.get(section_name, {}) or {}
    return getattr(full_config, section_name, None)


def _config_value(section, key: str, default):
    if isinstance(section, dict):
        return section.get(key, default)
    return getattr(section, key, default)


def _load_stored_national_release_history(ctx) -> list[dict]:
    task = getattr(ctx, "trade_trend_monitor_task", None)
    if task is not None and hasattr(task, "get_national_release_history"):
        return task.get_national_release_history()

    trade_config = _config_section(ctx, "trade_trend_monitor")
    repository_path = _config_value(
        trade_config,
        "state_file_path",
        "data/trade_trend_state.json",
    )
    return TradeTrendRepository(repository_path).get_national_release_history()


def _repository_from_config(ctx) -> TradeTrendRepository:
    trade_config = _config_section(ctx, "trade_trend_monitor")
    repository_path = _config_value(
        trade_config,
        "state_file_path",
        "data/trade_trend_state.json",
    )
    return TradeTrendRepository(repository_path)


def _national_trade_trend_client(ctx):
    client = getattr(ctx, "national_trade_trend_client", None)
    if client is not None and hasattr(client, "fetch_releases"):
        return client
    return NationalTradeTrendWebClient()


@router.get("/trade-trends/national/history")
async def get_national_trade_trend_history(
    include_recent: bool = Query(True),
    limit: int = Query(60, ge=1, le=300),
):
    """저장된 알림 이력과 공식 페이지 최신 후보를 함께 반환한다."""
    ctx = _get_ctx()
    stored_rows = _load_stored_national_release_history(ctx)

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


@router.post("/trade-trends/national/backfill")
async def backfill_national_trade_trend_history(
    year: int | None = Query(None, ge=2000, le=2100),
    max_detail_pages: int = Query(40, ge=1, le=200),
    list_page_count: int = Query(4, ge=1, le=20),
):
    """공식 페이지에서 지정 연도 수출입 발표를 가져와 저장 이력에 누적한다."""
    target_year = year or datetime.now().year
    ctx = _get_ctx()
    client = _national_trade_trend_client(ctx)

    releases = await client.fetch_releases(
        year=target_year,
        max_detail_pages=max_detail_pages,
        list_page_count=list_page_count,
    )
    repository = _repository_from_config(ctx)
    saved_count = 0
    for release in releases:
        if repository.save_national_release(release, source_type="backfill"):
            saved_count += 1
    stored_rows = repository.get_national_release_history()
    return {
        "success": True,
        "data": {
            "year": target_year,
            "fetched_count": len(releases),
            "saved_count": saved_count,
            "stored_count": len(stored_rows),
            "rows": stored_rows,
        },
    }
