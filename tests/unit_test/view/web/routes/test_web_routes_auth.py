"""
인증/공통 유틸리티 관련 테스트 (login.html, api_common).
"""
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException, Request, WebSocketException
from view.web import web_api
from view.web.security import CSRF_COOKIE_NAME, hash_password, issue_session


def test_login_success(web_client, mock_web_ctx):
    """POST /api/auth/login 로그인 성공 테스트"""
    response = web_client.post("/api/auth/login", data={"username": "admin", "password": "password"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "access_token" in response.cookies


def test_login_failure(web_client, mock_web_ctx):
    """POST /api/auth/login 로그인 실패 테스트"""
    response = web_client.post("/api/auth/login", data={"username": "wrong", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_login_issues_signed_expiring_session_and_csrf_cookie(web_client, mock_web_ctx):
    mock_web_ctx.full_config["auth"] = {
        "username": "admin",
        "password_hash": hash_password("password", iterations=1_000),
        "secret_key": "signing-secret",
        "session_max_age_seconds": 900,
    }
    web_client.cookies.clear()

    response = web_client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "password"},
    )

    assert response.status_code == 200
    assert response.cookies["access_token"] != "signing-secret"
    assert response.cookies[CSRF_COOKIE_NAME]
    set_cookie = response.headers.get_list("set-cookie")
    assert any("HttpOnly" in value and "Max-Age=900" in value for value in set_cookie)


def test_repeated_login_failures_are_rate_limited(web_client, mock_web_ctx):
    mock_web_ctx.full_config["auth"] = {
        "username": "admin",
        "password_hash": hash_password("password", iterations=1_000),
        "secret_key": "signing-secret",
        "login_max_failures": 2,
        "login_lockout_seconds": 60,
    }
    web_client.cookies.clear()

    for _ in range(2):
        response = web_client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401

    response = web_client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "password"},
    )
    assert response.status_code == 429


def test_get_ctx_uninitialized(web_client):
    """서비스 초기화 전 호출 시 503 에러 테스트"""
    from view.web import web_api
    original_ctx = web_api._ctx
    web_api.set_ctx(None)

    try:
        response = web_client.get("/api/status")
        assert response.status_code == 503
    finally:
        web_api.set_ctx(original_ctx)


def test_check_auth(mock_web_ctx):
    """check_auth 함수 테스트"""
    mock_request = MagicMock(spec=Request)
    auth_config = {"secret_key": "secret", "session_max_age_seconds": 3600}
    token, _ = issue_session(auth_config, "admin")
    mock_request.cookies = {"access_token": token}

    mock_web_ctx.full_config = {"auth": auth_config}
    mock_web_ctx.env.active_config = {}

    # Success
    assert web_api.check_auth(mock_request) is True

    # Fail
    mock_request.cookies = {"access_token": "wrong"}
    with pytest.raises(HTTPException) as exc:
        web_api.check_auth(mock_request)
    assert exc.value.status_code == 401


def test_check_auth_rejects_missing_auth_config_and_cookie(mock_web_ctx):
    """인증 설정과 쿠키가 모두 없어도 fail-open 되면 안 된다."""
    mock_request = MagicMock(spec=Request)
    mock_request.cookies = {}
    mock_web_ctx.full_config = {}
    mock_web_ctx.env.active_config = {}

    with pytest.raises(HTTPException) as exc:
        web_api.check_auth(mock_request)

    assert exc.value.status_code == 401


def test_check_auth_rejects_websocket_with_policy_violation(mock_web_ctx):
    """WebSocket 인증 실패는 handshake 단계에서 정책 위반으로 종료한다."""
    mock_connection = MagicMock()
    mock_connection.cookies = {}
    mock_connection.scope = {"type": "websocket"}
    mock_web_ctx.full_config = {"auth": {"secret_key": "secret"}}

    with pytest.raises(WebSocketException) as exc:
        web_api.check_auth(mock_connection)

    assert exc.value.code == 1008


def test_sensitive_api_rejects_request_without_cookie(web_client, mock_web_ctx):
    """로그인 페이지를 우회한 주문 API 직접 호출을 차단한다."""
    mock_web_ctx.full_config = {"auth": {"secret_key": "secret"}}
    web_client.cookies.clear()

    response = web_client.post(
        "/api/order",
        json={"code": "005930", "price": "70000", "qty": "1", "side": "buy"},
    )

    assert response.status_code == 401
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


def test_state_changing_api_rejects_missing_csrf(web_client, mock_web_ctx):
    web_client.cookies.pop(CSRF_COOKIE_NAME)
    web_client.headers.pop("X-CSRF-Token")

    response = web_client.post(
        "/api/order",
        json={"code": "005930", "price": "70000", "qty": "1", "side": "buy"},
    )

    assert response.status_code == 403
    mock_web_ctx.order_execution_service.handle_buy_stock.assert_not_awaited()


def test_serialize_helpers():
    """_serialize_response 및 _serialize_list_items 헬퍼 함수 테스트"""
    # _serialize_response
    assert web_api._serialize_response(None)["rt_cd"] == "999"

    class MockResp:
        def to_dict(self): return {"a": 1}
    assert web_api._serialize_response(MockResp()) == {"a": 1}

    class MockResp2:
        rt_cd = "1"
        msg1 = "msg"
        data = "data"
    assert web_api._serialize_response(MockResp2()) == {"rt_cd": "1", "msg1": "msg", "data": "data"}

    # _serialize_list_items
    items = [MockResp(), {"b": 2}, "string_item"]
    serialized = web_api._serialize_list_items(items)
    assert serialized[0] == {"a": 1}
    assert serialized[1] == {"b": 2}
    assert serialized[2] == "string_item"
