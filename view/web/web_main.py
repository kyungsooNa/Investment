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

# 공통 페이지 렌더링 함수 (로그인 체크 포함)
async def render_page(request: Request, template_name: str, active_page: str):
    try:
        ctx = web_api._get_ctx()
    except:
        return templates.TemplateResponse("login.html", {"request": request})

    use_login = ctx.full_config.get("use_login", True)
    if use_login:
        auth_config = ctx.full_config.get("auth", {})
        expected_token = auth_config.get("secret_key")
        token = request.cookies.get("access_token")

        if not token or token != expected_token:
            return templates.TemplateResponse("login.html", {"request": request})

    return templates.TemplateResponse(template_name, {"request": request, "active_page": active_page})

# 4. 페이지 라우팅
@app.get("/")
async def index(request: Request):
    return await render_page(request, "index.html", "stock")

@app.get("/balance")
async def balance(request: Request):
    return await render_page(request, "balance.html", "balance")

@app.get("/order")
async def order(request: Request):
    return await render_page(request, "order.html", "order")

@app.get("/ranking")
async def ranking(request: Request):
    return await render_page(request, "ranking.html", "ranking")

@app.get("/marketcap")
async def marketcap(request: Request):
    return await render_page(request, "marketcap.html", "marketcap")

@app.get("/virtual")
async def virtual(request: Request):
    return await render_page(request, "virtual.html", "virtual")

@app.get("/scheduler")
async def scheduler(request: Request):
    return await render_page(request, "scheduler.html", "scheduler")

@app.get("/program")
async def program(request: Request):
    return await render_page(request, "program.html", "program")

# 5. 로그아웃
@app.get("/logout")
async def logout():
    from fastapi.responses import RedirectResponse
    response = RedirectResponse(url="/")
    response.delete_cookie("access_token")
    return response