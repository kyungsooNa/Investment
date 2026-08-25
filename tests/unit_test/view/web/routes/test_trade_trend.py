from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import view.web.api_common as api_common
import view.web.web_main as web_main
from repositories.trade_trend_repository import TradeTrendRepository
from services.trade_trend_service import (
    NationalTradeTrendRelease,
    TradeStatItem,
    shift_yyyymm,
)


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


def test_national_trade_trend_history_reads_state_file_when_task_is_missing(tmp_path):
    state_file = tmp_path / "trade_trend_state.json"
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 8월 1~10일",
        export_amount_100m_usd=213,
        export_yoy_pct=45.3,
        export_daily_avg_100m_usd=30.4,
        semiconductor_export_amount_100m_usd=100,
        semiconductor_daily_avg_100m_usd=14.3,
        working_days_current=7.0,
        import_amount_100m_usd=195,
        import_yoy_pct=23.1,
        trade_balance_100m_usd=18,
        trade_balance_label="흑자",
        published_at="2026-08-11",
    )
    TradeTrendRepository(state_file).mark_national_release_sent(
        release,
        sent_at="2026-08-11T17:27:41+09:00",
    )
    ctx = SimpleNamespace(
        full_config={
            "use_login": False,
            "auth": {"secret_key": "test-token"},
            "trade_trend_monitor": {"state_file_path": str(state_file)},
        },
        trade_trend_monitor_task=None,
        national_trade_trend_client=None,
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).get(
            "/api/trade-trends/national/history?include_recent=false"
        )
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["stored_count"] == 1
    assert payload["data"]["rows"][0]["period_label"] == "2026년 8월 1~10일"
    assert payload["data"]["rows"][0]["semiconductor_export_amount_100m_usd"] == 100


def test_national_trade_trend_backfill_saves_year_releases(tmp_path):
    state_file = tmp_path / "trade_trend_state.json"
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 1월 1일 ~ 1월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/jan10",
        period_label="2026년 1월 1~10일",
        export_amount_100m_usd=180,
        import_amount_100m_usd=170,
        trade_balance_100m_usd=10,
        trade_balance_label="흑자",
        published_at="2026-01-11",
    )
    client = SimpleNamespace(
        fetch_releases=AsyncMock(return_value=[release]),
    )
    ctx = SimpleNamespace(
        full_config={
            "use_login": False,
            "auth": {"secret_key": "test-token"},
            "trade_trend_monitor": {"state_file_path": str(state_file)},
        },
        trade_trend_monitor_task=None,
        national_trade_trend_client=client,
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).post(
            "/api/trade-trends/national/backfill?year=2026&max_detail_pages=20&list_page_count=2"
        )
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["saved_count"] == 1
    assert payload["data"]["stored_count"] == 1
    assert TradeTrendRepository(state_file).get_national_release_history()[0]["source_type"] == "backfill"
    client.fetch_releases.assert_awaited_once_with(
        year=2026,
        max_detail_pages=20,
        list_page_count=2,
    )


def test_national_trade_trend_backfill_builds_client_when_missing(tmp_path, monkeypatch):
    state_file = tmp_path / "trade_trend_state.json"
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 1월 1일 ~ 1월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/jan10",
        period_label="2026년 1월 1~10일",
        export_amount_100m_usd=180,
        import_amount_100m_usd=170,
        trade_balance_100m_usd=10,
        trade_balance_label="흑자",
        published_at="2026-01-11",
    )
    fallback_client = SimpleNamespace(
        fetch_releases=AsyncMock(return_value=[release]),
    )
    monkeypatch.setattr(
        "view.web.routes.trade_trend.NationalTradeTrendWebClient",
        lambda: fallback_client,
    )
    ctx = SimpleNamespace(
        full_config={
            "use_login": False,
            "auth": {"secret_key": "test-token"},
            "trade_trend_monitor": {"state_file_path": str(state_file)},
        },
        trade_trend_monitor_task=None,
        national_trade_trend_client=None,
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).post(
            "/api/trade-trends/national/backfill?year=2026&max_detail_pages=20&list_page_count=2"
        )
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    assert response.json()["data"]["stored_count"] == 1
    fallback_client.fetch_releases.assert_awaited_once_with(
        year=2026,
        max_detail_pages=20,
        list_page_count=2,
    )


def test_jeju_region_trade_returns_series_from_customs_client():
    rows = [
        TradeStatItem("2025.05", "제주", "-", 10000000, 8000000, 2000000),
        TradeStatItem("2026.04", "제주", "-", 20000000, 9000000, 11000000),
        TradeStatItem("2026.05", "제주", "-", 25000000, 9900000, 15100000),
    ]
    customs_client = SimpleNamespace(
        fetch_sido_total_range=AsyncMock(return_value=rows),
    )
    ctx = SimpleNamespace(
        full_config={"use_login": False, "auth": {"secret_key": "test-token"}},
        trade_trend_customs_client=customs_client,
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).get("/api/trade-trends/jeju/region?months=2")
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["available"] is True
    assert len(payload["data"]["rows"]) == 2
    latest = payload["data"]["latest"]
    assert latest["period"] == "2026.05"
    assert latest["period_label"] == "2026년 5월"
    assert latest["export_amount_usd"] == 25000000
    assert latest["export_mom_pct"] == pytest.approx(25.0)
    assert latest["export_yoy_pct"] == pytest.approx(150.0)

    begin, end = customs_client.fetch_sido_total_range.await_args.args
    expected_end = shift_yyyymm(datetime.now().strftime("%Y%m"), -1)
    assert end == expected_end
    # 표시 2개월 + 전년동월 비교용 12개월을 함께 받아온다.
    assert begin == shift_yyyymm(expected_end, -13)


def test_jeju_region_trade_reports_unavailable_without_customs_client():
    ctx = SimpleNamespace(
        full_config={"use_login": False, "auth": {"secret_key": "test-token"}},
        trade_trend_customs_client=None,
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app).get("/api/trade-trends/jeju/region")
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["available"] is False
    assert payload["data"]["rows"] == []
    assert payload["data"]["latest"] is None


def test_jeju_region_trade_reports_unavailable_when_customs_client_fails():
    customs_client = SimpleNamespace(
        fetch_sido_total_range=AsyncMock(side_effect=RuntimeError("Internal Server Error")),
    )
    ctx = SimpleNamespace(
        full_config={"use_login": False, "auth": {"secret_key": "test-token"}},
        trade_trend_customs_client=customs_client,
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app, raise_server_exceptions=False).get(
            "/api/trade-trends/jeju/region"
        )
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["available"] is False
    assert payload["data"]["rows"] == []
    assert payload["data"]["latest"] is None
    assert payload["data"]["message"] == "관세청 수출입통계 API 연결 실패로 제주지역 동향을 일시적으로 조회할 수 없습니다."


def test_jeju_region_trade_reports_api_key_permission_error():
    customs_client = SimpleNamespace(
        fetch_sido_total_range=AsyncMock(
            side_effect=ValueError(
                "관세청 수출입통계 API 오류: 30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR"
            )
        ),
    )
    ctx = SimpleNamespace(
        full_config={"use_login": False, "auth": {"secret_key": "test-token"}},
        trade_trend_customs_client=customs_client,
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )
    api_common.set_ctx(ctx)

    try:
        response = TestClient(web_main.app, raise_server_exceptions=False).get(
            "/api/trade-trends/jeju/region"
        )
    finally:
        api_common.set_ctx(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["available"] is False
    assert payload["data"]["message"] == "관세청 시도별 수출입실적 API 키 권한을 확인해 주세요."
