"""운영자 대시보드 API 통합 테스트.

- GET /api/operator/status  → active_alerts, kill_switch, summary 포함
- GET /api/operator/alerts  → 전이 이력 반환
- POST /api/operator/alerts/{key}/resolve → 수동 해제
- kill_switch.trip() → /api/operator/status에 노출
- 동일 트립 반복 → notification 히스토리 중복 없음
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from view.web.routes import router as api_router
from view.web import api_common
from common.operator_alert_types import AlertSource, AlertTransition
from services.operator_alert_service import OperatorAlertService
from tests.web_auth_helpers import authenticated_client_options


# ── 픽스처 ─────────────────────────────────────────────────────────

def _make_notif():
    svc = MagicMock()
    svc.emit = AsyncMock(return_value=None)
    return svc


def _make_market_clock():
    return MagicMock()


def _make_ks_status(is_tripped: bool = False):
    return {
        "is_tripped": is_tripped,
        "trip_reason": "테스트 트립" if is_tripped else None,
        "trip_timestamp": None,
        "daily_realized_loss_won": 0,
        "consecutive_losses": 0,
        "consecutive_api_errors": 0,
        "strategy_tripped": {},
        "thresholds": {},
    }


def _make_ctx(operator_alert_svc, ks_is_tripped=False):
    mock_ctx = MagicMock()
    mock_ctx.initialized = True
    mock_ctx.full_config = {
        "use_login": False,
        "auth": {
            "username": "test-operator",
            "secret_key": "test",
            "session_max_age_seconds": 3600,
        },
    }
    mock_ctx.operator_alert_service = operator_alert_svc
    mock_ctx.kill_switch_service = MagicMock()
    mock_ctx.kill_switch_service.get_status.return_value = _make_ks_status(ks_is_tripped)
    return mock_ctx


@pytest.fixture
def notif():
    return _make_notif()


@pytest.fixture
def alert_svc(notif, tmp_path):
    return OperatorAlertService(
        notification_service=notif,
        market_clock=_make_market_clock(),
        state_file_path=str(tmp_path / "state.json"),
    )


@pytest.fixture
def web_app():
    app = FastAPI()
    app.include_router(api_router)
    return app


@pytest.fixture
def client(web_app, alert_svc):
    ctx = _make_ctx(alert_svc)
    api_common.set_ctx(ctx)
    with TestClient(web_app, **authenticated_client_options(ctx)) as c:
        yield c, alert_svc
    api_common.set_ctx(None)


@pytest.fixture
def client_tripped(web_app, alert_svc):
    """Kill Switch가 트립된 상태."""
    ctx = _make_ctx(alert_svc, ks_is_tripped=True)
    api_common.set_ctx(ctx)
    with TestClient(web_app, **authenticated_client_options(ctx)) as c:
        yield c, alert_svc
    api_common.set_ctx(None)


# ── GET /api/operator/status ────────────────────────────────────────


def test_status_empty(client):
    c, _ = client
    r = c.get("/api/operator/status")
    assert r.status_code == 200
    data = r.json()
    assert data["active_alerts"] == []
    assert data["summary"]["total_active"] == 0
    assert data["summary"]["has_critical"] is False


def test_status_shows_kill_switch_tripped(client_tripped):
    c, _ = client_tripped
    r = c.get("/api/operator/status")
    assert r.status_code == 200
    data = r.json()
    assert data["kill_switch"]["is_tripped"] is True
    assert data["summary"]["has_kill_switch"] is True


@pytest.mark.asyncio
async def test_status_shows_active_alert_after_report(web_app, alert_svc):
    """report 후 /api/operator/status에 active_alerts 노출."""
    await alert_svc.report(
        AlertSource.KILL_SWITCH, "kill_switch:global",
        "critical", "Kill Switch 트립", "사유: 테스트",
    )
    ctx = _make_ctx(alert_svc, ks_is_tripped=True)
    api_common.set_ctx(ctx)
    try:
        with TestClient(web_app, **authenticated_client_options(ctx)) as c:
            r = c.get("/api/operator/status")
        assert r.status_code == 200
        data = r.json()
        keys = [a["dedup_key"] for a in data["active_alerts"]]
        assert "kill_switch:global" in keys
        assert data["summary"]["total_active"] == 1
        assert data["summary"]["has_critical"] is True
    finally:
        api_common.set_ctx(None)


# ── GET /api/operator/alerts ────────────────────────────────────────


@pytest.mark.asyncio
async def test_alerts_history_after_report(web_app, alert_svc):
    await alert_svc.report(
        AlertSource.RISK_GATE, "risk_gate:daily_cap:S1:005930",
        "block", "일일 한도 초과", "메시지",
    )
    ctx = _make_ctx(alert_svc)
    api_common.set_ctx(ctx)
    try:
        with TestClient(web_app, **authenticated_client_options(ctx)) as c:
            r = c.get("/api/operator/alerts")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        assert any(h["dedup_key"] == "risk_gate:daily_cap:S1:005930" for h in data["alerts"])
    finally:
        api_common.set_ctx(None)


@pytest.mark.asyncio
async def test_alerts_source_filter(web_app, alert_svc):
    await alert_svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    await alert_svc.report(AlertSource.RISK_GATE, "risk_gate:rule:S1:000001", "block", "T2", "M2")

    ctx = _make_ctx(alert_svc)
    api_common.set_ctx(ctx)
    try:
        with TestClient(web_app, **authenticated_client_options(ctx)) as c:
            r = c.get("/api/operator/alerts?source=KILL_SWITCH")
        data = r.json()
        assert all(h["source"] == "KILL_SWITCH" for h in data["alerts"])
    finally:
        api_common.set_ctx(None)


# ── POST /api/operator/alerts/{key}/resolve ─────────────────────────


@pytest.mark.asyncio
async def test_resolve_endpoint(web_app, alert_svc, notif):
    await alert_svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    assert "kill_switch:global" in alert_svc._active

    ctx = _make_ctx(alert_svc)
    api_common.set_ctx(ctx)
    try:
        with TestClient(web_app, **authenticated_client_options(ctx)) as c:
            r = c.post("/api/operator/alerts/kill_switch%3Aglobal/resolve?reason=운영자+해제")
        assert r.status_code == 200
        data = r.json()
        assert data["resolved"] is True
        assert "kill_switch:global" not in alert_svc._active
    finally:
        api_common.set_ctx(None)


@pytest.mark.asyncio
async def test_resolve_nonexistent_returns_false(web_app, alert_svc):
    ctx = _make_ctx(alert_svc)
    api_common.set_ctx(ctx)
    try:
        with TestClient(web_app, **authenticated_client_options(ctx)) as c:
            r = c.post("/api/operator/alerts/kill_switch%3Aglobal/resolve")
        data = r.json()
        assert data["resolved"] is False
    finally:
        api_common.set_ctx(None)


# ── 중복 report 알림 억제 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_trip_no_extra_notification(alert_svc, notif):
    """동일 key 동일 severity 3회 보고 → emit은 1회만."""
    for _ in range(3):
        await alert_svc.report(
            AlertSource.KILL_SWITCH, "kill_switch:global",
            "critical", "Kill Switch 트립", "사유",
        )
    assert notif.emit.await_count == 1


@pytest.mark.asyncio
async def test_escalation_emits_second_time(alert_svc, notif):
    """severity 상승 시 ESCALATED emit 발생 → 총 2회."""
    await alert_svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "warning", "T", "M")
    await alert_svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T2", "M2")
    assert notif.emit.await_count == 2
