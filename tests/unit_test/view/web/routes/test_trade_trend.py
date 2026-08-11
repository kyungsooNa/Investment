from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import view.web.api_common as api_common
import view.web.web_main as web_main
from services.trade_trend_service import NationalTradeTrendRelease


def test_national_trade_trend_history_merges_stored_and_recent_rows():
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 8월 1~10일",
        export_amount_100m_usd=301,
        export_yoy_pct=12.0,
        export_daily_avg_100m_usd=30.1,
        export_daily_avg_mom_pct=5.0,
        semiconductor_export_amount_100m_usd=101,
        semiconductor_yoy_pct=55.0,
        semiconductor_daily_avg_100m_usd=10.1,
        semiconductor_daily_avg_mom_pct=8.0,
        working_days_current=10.0,
        import_amount_100m_usd=250,
        import_yoy_pct=4.0,
        trade_balance_100m_usd=51,
        trade_balance_label="흑자",
        published_at="2026-08-11",
    )
    stored = {
        "source": "customs",
        "source_type": "sent",
        "phase": release.phase,
        "title": release.title,
        "url": release.url,
        "period_label": release.period_label,
        "export_amount_100m_usd": release.export_amount_100m_usd,
        "export_yoy_pct": release.export_yoy_pct,
        "export_daily_avg_100m_usd": release.export_daily_avg_100m_usd,
        "export_daily_avg_mom_pct": release.export_daily_avg_mom_pct,
        "semiconductor_export_amount_100m_usd": release.semiconductor_export_amount_100m_usd,
        "semiconductor_yoy_pct": release.semiconductor_yoy_pct,
        "semiconductor_daily_avg_100m_usd": release.semiconductor_daily_avg_100m_usd,
        "semiconductor_daily_avg_mom_pct": release.semiconductor_daily_avg_mom_pct,
        "working_days_current": release.working_days_current,
        "import_amount_100m_usd": release.import_amount_100m_usd,
        "import_yoy_pct": release.import_yoy_pct,
        "trade_balance_100m_usd": release.trade_balance_100m_usd,
        "trade_balance_label": release.trade_balance_label,
        "published_at": release.published_at,
        "highlights": [],
        "sent_at": "2026-08-11T09:30:00",
        "dedup_key": release.dedup_key,
    }
    ctx = SimpleNamespace(
        full_config={"use_login": False, "auth": {"secret_key": "test-token"}},
        trade_trend_monitor_task=SimpleNamespace(
            get_national_release_history=lambda: [stored],
        ),
        national_trade_trend_client=SimpleNamespace(
            fetch_recent_releases=AsyncMock(return_value=[release]),
        ),
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).get("/api/trade-trends/national/history")
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert len(payload["data"]["rows"]) == 1
    assert payload["data"]["rows"][0]["source_type"] == "sent"
    assert payload["data"]["rows"][0]["period_label"] == "2026년 8월 1~10일"
    assert payload["data"]["rows"][0]["semiconductor_export_amount_100m_usd"] == 101
    assert payload["data"]["rows"][0]["working_days_current"] == 10.0
