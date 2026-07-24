"""인증 관련 API 엔드포인트 (login.html)."""
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from view.web.api_common import get_auth_config, _config_get, _get_ctx
from view.web.deployment_policy import is_public_mode
from view.web.security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    issue_session,
    login_attempt_limiter,
    verify_password,
)

router = APIRouter()


@router.post("/auth/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    auth_config = get_auth_config()
    expected_username = _config_get(auth_config, "username")
    password_hash = _config_get(auth_config, "password_hash")
    secret_key = _config_get(auth_config, "secret_key")
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = (client_ip, username)

    if login_attempt_limiter.is_blocked(attempt_key, auth_config):
        return JSONResponse(
            content={"success": False, "msg": "로그인을 처리할 수 없습니다. 잠시 후 다시 시도하세요."},
            status_code=429,
        )

    credentials_configured = all(
        isinstance(value, str) and bool(value)
        for value in (expected_username, password_hash, secret_key)
    )
    username_matches = (
        isinstance(expected_username, str)
        and secrets.compare_digest(username, expected_username)
    )
    if credentials_configured and username_matches and verify_password(password, password_hash):
        login_attempt_limiter.record_success(attempt_key)
        token, claims = issue_session(auth_config, expected_username)
        max_age = int(_config_get(auth_config, "session_max_age_seconds", 3600))
        secure_cookie = bool(_config_get(auth_config, "secure_cookie", False)) or is_public_mode(_get_ctx())
        response = JSONResponse(content={"success": True})
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=max_age,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=claims.csrf_token,
            max_age=max_age,
            httponly=False,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return response

    login_attempt_limiter.record_failure(attempt_key, auth_config)
    return JSONResponse(content={"success": False, "msg": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
