import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 프로젝트 내부 모듈 임포트
from view.web.web_app_initializer import WebAppContext
import view.web.web_api as web_api
import view.web.api_common as api_common

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

    # 4. 전략 스케줄러 초기화 + 이전 상태 복원
    ctx.initialize_scheduler()
    await ctx.scheduler.restore_state()

    # 백그라운드 태스크 시작 (데이터 Flush 등)
    ctx.start_background_tasks()

    print("=== 웹 서비스 초기화 완료 ===")
    yield

    # 종료 시 정리 (데이터 Flush)
    await ctx.shutdown()

    # 종료 시 스케줄러 상태 저장 후 정지
    if ctx.scheduler and ctx.scheduler._running:
        await ctx.scheduler.stop(save_state=True)

# 1. FastAPI 앱 인스턴스 생성 (lifespan 추가)
app = FastAPI(title="Trading App", lifespan=lifespan)

# debugpy가 요청 처리 컨텍스트를 인식하도록 첫 요청에서 트리거
import sys
if "debugpy" in sys.modules:
    _debugpy_activated = False

    @app.middleware("http")
    async def _debugpy_activate_middleware(request: Request, call_next):
        global _debugpy_activated
        if not _debugpy_activated:
            _debugpy_activated = True
            import debugpy
            debugpy.debug_this_thread()
        return await call_next(request)

# --- Foreground 우선순위 미들웨어 ---
# Broker API를 호출하는 라우트만 foreground로 래핑하여
# 백그라운드 태스크(RankingTask, WebSocketWatchdog 등)와의 API rate limit 경합을 방지한다.

_FOREGROUND_PATHS = frozenset({
    # stock.py — 현재가, 차트, 기술지표
    "/api/stock/",
    "/api/chart/",
    "/api/indicator/",
    # balance.py
    "/api/balance",
    # order.py
    "/api/order",
    # ranking.py
    "/api/ranking/",
    "/api/top-market-cap",
    # program.py — broker API 호출하는 엔드포인트만
    "/api/program-trading/subscribe",
    "/api/program-trading/history/",
    "/api/program-trading/unsubscribe",
    # scheduler.py — start/stop/strategy 제어
    "/api/scheduler/start",
    "/api/scheduler/stop",
    "/api/scheduler/strategy/",
    # virtual.py — broker API 호출하는 엔드포인트만
    "/api/virtual/chart/",
    "/api/virtual/history",
})

_FOREGROUND_EXCLUDE = frozenset({
    "/api/ranking/progress",
    "/api/stock/search",
})


def _needs_foreground(path: str) -> bool:
    """경로가 foreground 우선순위 적용 대상인지 판단."""
    if path in _FOREGROUND_EXCLUDE:
        return False
    return any(path.startswith(prefix) for prefix in _FOREGROUND_PATHS)


@app.middleware("http")
async def foreground_priority_middleware(request: Request, call_next):
    """Broker API 호출 라우트에 foreground 우선순위를 적용하는 미들웨어."""
    path = request.url.path
    ctx = api_common._ctx
    fg = getattr(ctx, 'foreground_scheduler', None) if ctx else None

    if fg and _needs_foreground(path):
        async with fg.context():
            return await call_next(request)
    return await call_next(request)


# 2. 정적 파일 및 템플릿 설정
# 현재 파일(web_main.py)의 위치를 기준으로 절대 경로 설정하여 실행 위치에 영향받지 않도록 함
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# 3. API 라우터 등록
app.include_router(web_api.router)

# 페이지 라우터 생성
page_router = APIRouter()

# 공통 페이지 렌더링 함수 (로그인 체크 포함)
async def render_page(request: Request, template_name: str, active_page: str, extra_context: dict = None):
    try:
        ctx = web_api._get_ctx()
    except:
        return templates.TemplateResponse(request, "login.html")

    use_login = ctx.full_config.get("use_login", True)
    if use_login:
        auth_config = ctx.full_config.get("auth", {})
        expected_token = auth_config.get("secret_key")
        token = request.cookies.get("access_token")

        if not token or token != expected_token:
            return templates.TemplateResponse(request, "login.html")

    context = {"active_page": active_page}
    if extra_context:
        context.update(extra_context)
    return templates.TemplateResponse(request, template_name, context)

# 4. 페이지 라우팅
@page_router.get("/")
async def index(request: Request):
    return await render_page(request, "index.html", "home")

@page_router.get("/stock")
async def stock(request: Request):
    # 종목 리스트를 페이지 로드 시 1회 전달 (클라이언트 자동완성용)
    try:
        ctx = web_api._get_ctx()
        stock_list = [
            {"c": code, "n": name}
            for name, code in ctx.stock_code_mapper.name_to_code.items()
        ]
    except Exception:
        stock_list = []
    return await render_page(request, "stock.html", "stock", extra_context={"stock_list": stock_list})

@page_router.get("/balance")
async def balance(request: Request):
    return await render_page(request, "balance.html", "balance")

@page_router.get("/order")
async def order(request: Request):
    return await render_page(request, "order.html", "order")

@page_router.get("/ranking")
async def ranking(request: Request):
    return await render_page(request, "ranking.html", "ranking")

@page_router.get("/marketcap")
async def marketcap(request: Request):
    return await render_page(request, "marketcap.html", "marketcap")

@page_router.get("/virtual")
async def virtual(request: Request):
    return await render_page(request, "virtual.html", "virtual")

@page_router.get("/scheduler")
async def scheduler(request: Request):
    return await render_page(request, "scheduler.html", "scheduler")

@page_router.get("/program")
async def program(request: Request):
    return await render_page(request, "program.html", "program")

@page_router.get("/system")
async def system(request: Request):
    return await render_page(request, "system.html", "system")

# 5. 로그아웃
@page_router.get("/logout")
async def logout():
    from fastapi.responses import RedirectResponse
    response = RedirectResponse(url="/")
    response.delete_cookie("access_token")
    return response

app.include_router(page_router)