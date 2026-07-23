"""
인증 관련 API 엔드포인트 (login.html).
"""
from fastapi import APIRouter, Form, Response
from fastapi.responses import JSONResponse
from view.web.api_common import get_auth_value

router = APIRouter()


@router.post("/auth/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    expected_username = get_auth_value("username")
    expected_password = get_auth_value("password")
    secret_key = get_auth_value("secret_key")

    credentials_configured = all(
        isinstance(value, str) and bool(value)
        for value in (expected_username, expected_password, secret_key)
    )
    if (
        credentials_configured
        and username == expected_username
        and password == expected_password
    ):
        response = JSONResponse(content={"success": True})
        # 쿠키 설정
        response.set_cookie(
            key="access_token",
            value=secret_key,
            httponly=True,
            samesite="lax" # 로컬 테스트 시 안정성 위함
        )
        return response

    return JSONResponse(content={"success": False, "msg": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
