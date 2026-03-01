"""
인증 관련 API 엔드포인트 (login.html).
"""
from fastapi import APIRouter, Form, Response
from fastapi.responses import JSONResponse
from view.web.api_common import _get_ctx

router = APIRouter()


@router.post("/auth/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    ctx = _get_ctx()
    auth_config = ctx.full_config.get("auth", {})

    print(f"\n=== 로그인 시도 ===")
    print(f"입력 ID: {username} / PW: {password}")
    print(f"설정 ID: {auth_config.get('username')} / PW: {auth_config.get('password')}")
    print(f"==================\n")

    if username == auth_config.get("username") and password == auth_config.get("password"):
        response = JSONResponse(content={"success": True})
        # 쿠키 설정
        response.set_cookie(
            key="access_token",
            value=auth_config.get("secret_key"),
            httponly=True,
            samesite="lax" # 로컬 테스트 시 안정성 위함
        )
        return response

    return JSONResponse(content={"success": False, "msg": "아이디 또는 비밀번호가 틀렸습니다."}, status_code=401)
