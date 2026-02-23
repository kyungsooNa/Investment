import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 프로젝트 내부 모듈 임포트
from view.web.web_app_initializer import WebAppContext
import view.web.web_api as web_api

# [추가] 서버 시작 시 초기화 로직
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 초기화 객체 생성 (app_context 대용으로 빈 객체 전달)
    class SimpleContext: env = None
    ctx = WebAppContext(SimpleContext())
    
    # 2. 환경 설정 로드 및 서비스 초기화
    ctx.load_config_and_env()
    await ctx.initialize_services(is_paper_trading=True) # 기본 모의투자 설정
    
    # 3. web_api에 완성된 ctx 연결 (이게 없어서 503 에러가 났던 것임)
    web_api.set_ctx(ctx)

    # 4. 전략 스케줄러 초기화 (자동 시작 아님, 웹 UI에서 수동 시작)
    ctx.initialize_scheduler()

    print("=== 웹 서비스 초기화 완료 ===")
    yield

    # 종료 시 스케줄러 정지
    if ctx.scheduler and ctx.scheduler._running:
        await ctx.scheduler.stop()

# 1. FastAPI 앱 인스턴스 생성 (lifespan 추가)
app = FastAPI(title="Trading App", lifespan=lifespan)

# 2. 정적 파일 및 템플릿 설정
app.mount("/static", StaticFiles(directory="view/web/static"), name="static")
templates = Jinja2Templates(directory="view/web/templates")

# 3. API 라우터 등록
app.include_router(web_api.router)

# 4. 메인 페이지 로직
@app.get("/")
async def index(request: Request):
    try:
        ctx = web_api._get_ctx()
    except:
        return templates.TemplateResponse("login.html", {"request": request})

    # [수정] use_login 설정 확인
    use_login = ctx.full_config.get("use_login", True)
    if not use_login:
        return templates.TemplateResponse("index.html", {"request": request})
    auth_config = ctx.full_config.get("auth", {})
    
    # 로그인 체크 로직
    expected_token = auth_config.get("secret_key")
    token = request.cookies.get("access_token")

    if not token or token != expected_token:
        return templates.TemplateResponse("login.html", {"request": request})

    return templates.TemplateResponse("index.html", {"request": request})

# 5. 로그아웃
@app.get("/logout")
async def logout():
    from fastapi.responses import RedirectResponse
    response = RedirectResponse(url="/")
    response.delete_cookie("access_token")
    return response