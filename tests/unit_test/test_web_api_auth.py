"""
인증/공통 유틸리티 관련 테스트 (login.html, api_common).
"""
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException, Request
from view.web import web_api


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
    mock_request.cookies = {"access_token": "secret"}

    mock_web_ctx.env.active_config = {"auth": {"secret_key": "secret"}}

    # Success
    assert web_api.check_auth(mock_request) is True

    # Fail
    mock_request.cookies = {"access_token": "wrong"}
    with pytest.raises(HTTPException) as exc:
        web_api.check_auth(mock_request)
    assert exc.value.status_code == 401


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
