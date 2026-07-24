"""인증 관련 API 엔드포인트 (login.html)."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from view.web.api_common import (
    _config_get,
    _get_ctx,
    check_auth,
    check_role_for_request,
    get_auth_config,
    get_authenticated_principal,
)
from view.web.deployment_policy import is_public_mode
from view.web.security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    issue_session,
    login_attempt_limiter,
)
from view.web.user_repository import ConfigUserRepository

router = APIRouter()


@router.post("/auth/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    auth_config = get_auth_config()
    secret_key = _config_get(auth_config, "secret_key")
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = (client_ip, username)

    if login_attempt_limiter.is_blocked(attempt_key, auth_config):
        return JSONResponse(
            content={"success": False, "msg": "로그인을 처리할 수 없습니다. 잠시 후 다시 시도하세요."},
            status_code=429,
        )

    user = ConfigUserRepository(auth_config).authenticate(username, password)
    if isinstance(secret_key, str) and secret_key and user is not None:
        login_attempt_limiter.record_success(attempt_key)
        token, claims = issue_session(
            auth_config,
            user.username,
            role=user.role,
        )
        max_age = int(_config_get(auth_config, "session_max_age_seconds", 3600))
        secure_cookie = bool(_config_get(auth_config, "secure_cookie", False)) or is_public_mode(_get_ctx())
        response = JSONResponse(
            content={
                "success": True,
                "username": user.username,
                "role": user.role,
            }
        )
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


@router.get(
    "/auth/me",
    dependencies=[
        Depends(check_auth),
        Depends(check_role_for_request),
    ],
)
async def auth_me(request: Request):
    claims = get_authenticated_principal(request)
    return {"username": claims.username, "role": claims.role}
