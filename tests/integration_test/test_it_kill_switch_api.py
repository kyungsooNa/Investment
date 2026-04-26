"""
Kill Switch API 통합 테스트.

GET  /api/kill-switch/status  → 현재 상태 반환
POST /api/kill-switch/trip    → 수동 트립
POST /api/kill-switch/reset   → 수동 해제
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from view.web.routes import router as api_router
from view.web import api_common


def _make_ks_service(is_tripped: bool = False):
    ks = MagicMock()
    ks.get_status.return_value = {
        "is_tripped": is_tripped,
        "trip_reason": "수동 트립" if is_tripped else None,
        "trip_timestamp": None,
        "daily_realized_loss_won": 0,
        "consecutive_losses": 0,
        "consecutive_api_errors": 0,
        "thresholds": {
            "daily_loss_threshold_won": 1_000_000,
            "max_consecutive_losses": 3,
            "max_consecutive_api_errors": 10,
            "abnormal_fill_deviation_pct": 3.0,
        },
    }
    ks.manual_trip = AsyncMock()
    ks.manual_reset = AsyncMock()
    return ks


_SECRET = "test-token"

def _make_ctx(ks_service):
    mock_ctx = MagicMock()
    mock_ctx.initialized = True
    mock_ctx.full_config = {"use_login": False, "auth": {"secret_key": _SECRET}}
    mock_ctx.env.active_config = {"auth": {"secret_key": _SECRET}}
    mock_ctx.kill_switch_service = ks_service
    return mock_ctx


@pytest.fixture
def web_app():
    app = FastAPI()
    app.include_router(api_router)
    return app


@pytest.fixture
def ks_client(web_app):
    """Kill Switch 서비스가 정상 상태인 테스트 클라이언트."""
    ks_svc = _make_ks_service(is_tripped=False)
    api_common.set_ctx(_make_ctx(ks_svc))
    with TestClient(web_app, cookies={"access_token": _SECRET}) as client:
        yield client, ks_svc
    api_common.set_ctx(None)


@pytest.fixture
def ks_tripped_client(web_app):
    """Kill Switch가 트립된 상태의 테스트 클라이언트."""
    ks_svc = _make_ks_service(is_tripped=True)
    api_common.set_ctx(_make_ctx(ks_svc))
    with TestClient(web_app, cookies={"access_token": _SECRET}) as client:
        yield client, ks_svc
    api_common.set_ctx(None)


@pytest.fixture
def no_ks_client(web_app):
    """Kill Switch 서비스가 None인 테스트 클라이언트 (503 검증용)."""
    api_common.set_ctx(_make_ctx(ks_service=None))
    with TestClient(web_app, cookies={"access_token": _SECRET}) as client:
        yield client
    api_common.set_ctx(None)


def test_get_status_normal(ks_client):
    """정상 상태의 Kill Switch 상태 조회 — 200 + is_tripped=False."""
    client, _ = ks_client
    resp = client.get("/api/kill-switch/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_tripped"] is False
    assert "thresholds" in data


def test_get_status_tripped(ks_tripped_client):
    """트립 상태의 Kill Switch 상태 조회 — 200 + is_tripped=True + trip_reason."""
    client, _ = ks_tripped_client
    resp = client.get("/api/kill-switch/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_tripped"] is True
    assert data["trip_reason"] == "수동 트립"


def test_trip_kill_switch(ks_client):
    """수동 트립 API — 200 + ok=True + manual_trip 호출 확인."""
    client, ks_svc = ks_client
    resp = client.post(
        "/api/kill-switch/trip",
        json={"reason": "테스트 트립"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    ks_svc.manual_trip.assert_awaited_once()
    assert ks_svc.manual_trip.call_args.args[0] == "테스트 트립"


def test_reset_kill_switch(ks_tripped_client):
    """수동 해제 API — 200 + ok=True + manual_reset 호출 확인."""
    client, ks_svc = ks_tripped_client
    resp = client.post("/api/kill-switch/reset")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    ks_svc.manual_reset.assert_awaited_once()


def test_status_service_unavailable(no_ks_client):
    """kill_switch_service=None 일 때 GET status → 503."""
    resp = no_ks_client.get("/api/kill-switch/status")
    assert resp.status_code == 503


def test_trip_service_unavailable(no_ks_client):
    """kill_switch_service=None 일 때 POST trip → 503."""
    resp = no_ks_client.post(
        "/api/kill-switch/trip", json={"reason": "test"}
    )
    assert resp.status_code == 503


def test_reset_service_unavailable(no_ks_client):
    """kill_switch_service=None 일 때 POST reset → 503."""
    resp = no_ks_client.post("/api/kill-switch/reset")
    assert resp.status_code == 503
