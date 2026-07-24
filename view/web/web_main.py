import asyncio
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# 프로젝트 내부 모듈 임포트
from view.web.web_app_initializer import WebAppContext
import view.web.web_api as web_api
import view.web.api_common as api_common
from view.web.authorization import ADMIN, OPERATOR, VIEWER, role_allows
from view.web.deployment_policy import is_host_allowed

# ── 진단 전용 HTTP 서버 (포트 8001, 별도 OS 스레드) ──────────────────────
# asyncio 이벤트 루프가 완전히 블록되어도 응답 가능.
# 브라우저/curl 어디서든 http://127.0.0.1:8001/debug/requests 로 확인.

_DEBUG_SERVER_PORT = 8001


class _DebugHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/debug/requests":
            self.send_response(404)
            self.end_headers()
            return
        now = time.monotonic()
        rows = sorted(
            [
                {
                    "path": r["path"],
                    "method": r["method"],
                    "elapsed_sec": round(now - r["start"], 1),
                    "query": r["query"],
                }
                for r in api_common._active_requests.values()
            ],
            key=lambda x: x["elapsed_sec"],
            reverse=True,
        )
        body = json.dumps(
            {"count": len(rows), "data": rows, "recent": list(api_common._recent_completed)},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")  # 브라우저 콘솔 fetch 허용
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # 클라이언트가 응답을 받기 전에 연결을 끊은 경우 무시

    def log_message(self, *_):
        pass  # 로그 억제


def _start_debug_server():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", _DEBUG_SERVER_PORT), _DebugHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True, name="dbg-http")
        t.start()
        print(f"[DEBUG] 진단 서버 시작: http://127.0.0.1:{_DEBUG_SERVER_PORT}/debug/requests")
    except OSError as e:
        print(f"[DEBUG] 진단 서버 시작 실패 (포트 {_DEBUG_SERVER_PORT} 사용 중?): {e}")


_start_debug_server()


def _is_ignorable_connection_reset(exc: BaseException | None) -> bool:
    """Windows Proactor가 끊긴 HTTP/SSE 소켓 정리 중 올리는 잡음성 예외인지 판단."""
    if not isinstance(exc, ConnectionResetError):
        return False
    return getattr(exc, "winerror", None) in (None, 10054)


def _install_asyncio_exception_filter() -> None:
    """브라우저 연결 해제에서 파생된 WinError 10054 로그만 억제한다."""
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def _handler(loop, context):
        exc = context.get("exception")
        if _is_ignorable_connection_reset(exc):
            return
        if previous_handler:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


# [추가] 서버 시작 시 초기화 로직
@asynccontextmanager
async def lifespan(app: FastAPI):
    _install_asyncio_exception_filter()

    # 1. 초기화 객체 생성 (app_context 대용으로 빈 객체 전달)
    from view.web.bootstrap.runtime_mode import RuntimeMode
    runtime_mode = RuntimeMode.from_env()
    class SimpleContext: env = None
    ctx = WebAppContext(SimpleContext(), runtime_mode=runtime_mode)

    # 2. 환경 설정 로드 및 서비스 초기화
    ctx.load_config_and_env()
    await ctx.initialize_services(is_paper_trading=True) # 기본 모의투자 설정

    # 3. web_api에 완성된 ctx 연결 (이게 없어서 503 에러가 났던 것임)
    web_api.set_ctx(ctx)

    # 4. 전략 스케줄러 초기화 (상태 복원은 BackgroundScheduler 어댑터에서 단일 진입).
    # TRADING 비활성이면 StrategyFactory 가 내부에서 no-op.
    ctx.initialize_scheduler()

    # 4-1. 전략 state(positions/cooldown) 를 명시적으로 await 로드.
    # __init__ 의 fire-and-forget create_task 만으로는 scan 전에 로드 완료가 보장되지 않는다.
    await ctx.ensure_strategy_states_loaded()

    # 5. 실전 주문 상태 복원 및 reconcile (WebSocket 구독 전, 전략 스케줄러 전).
    # WEB / TRADING 어느 한쪽이라도 켜져 있으면 수행 — /api/order 가 살아 있는 한
    # broker 와 동기화 안 된 상태로 신규 주문이 나가는 위험을 차단해야 한다.
    if ctx.order_execution_service and (
        ctx.runtime_mode & (RuntimeMode.WEB | RuntimeMode.TRADING)
    ):
        await ctx.order_execution_service.restore_state_from_broker()
        await ctx.order_execution_service.reconcile_orders_with_broker()

    # 백그라운드 태스크 시작 — StrategySchedulerTaskAdapter 가 restore_state() 호출
    ctx.start_background_tasks()

    print("=== 웹 서비스 초기화 완료 ===")
    yield

    # 종료 시 정리 (데이터 Flush)
    await ctx.shutdown()

    # 종료 시 스케줄러 상태 저장 후 정지
    if ctx.scheduler and ctx.scheduler._running:
        await ctx.scheduler.stop(save_state=True)

    # StrategyStateIO 백그라운드 save task 가 남아 있으면 flush.
    from utils.strategy_state_io import StrategyStateIO
    await StrategyStateIO.flush_pending(timeout=5.0)

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

# --- 요청 추적 미들웨어 (hang 진단용) ---
# /api/* 요청의 시작~완료를 api_common._active_requests에 기록한다.
# /api/debug/requests 엔드포인트가 이 데이터를 읽어 in-flight 요청 목록을 반환한다.

async def request_tracker_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/debug/requests":
        return await call_next(request)
    req_id = uuid.uuid4().hex[:8]
    api_common._active_requests[req_id] = {
        "path": path,
        "method": request.method,
        "start": time.monotonic(),
        "query": str(request.url.query) or None,
    }
    start = time.monotonic()
    try:
        return await call_next(request)
    finally:
        elapsed = round(time.monotonic() - start, 2)
        api_common._active_requests.pop(req_id, None)
        # 완료 이력 기록 (hang 직전 분석용)
        rec = api_common._recent_completed
        rec.append({"path": path, "elapsed_sec": elapsed, "at": round(start, 1)})
        if len(rec) > api_common._RECENT_MAX:
            del rec[0]


async def public_host_middleware(request: Request, call_next):
    ctx = api_common._ctx
    if ctx is not None and not is_host_allowed(ctx, request.headers.get("host", "")):
        return JSONResponse(status_code=400, content={"detail": "Invalid host"})
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
    "/api/overseas/",
    # ranking.py
    "/api/ranking/",
    "/api/top-market-cap",
    # program.py — broker API 호출하는 엔드포인트만
    "/api/program-trading/subscribe",
    "/api/program-trading/history/",
    "/api/program-trading/unsubscribe",
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


async def foreground_priority_middleware(request: Request, call_next):
    """Broker API 호출 라우트에 foreground 우선순위를 적용하는 미들웨어."""
    path = request.url.path
    ctx = api_common._ctx
    fg = getattr(ctx, 'foreground_scheduler', None) if ctx else None

    if fg and _needs_foreground(path):
        async with fg.context():
            return await call_next(request)
    return await call_next(request)


_AUTH_EXEMPT_API_PATHS = frozenset({
    "/api/auth/login",
})


async def api_auth_middleware(request: Request, call_next):
    """인증되지 않은 API 요청을 foreground 자원 획득 전에 차단한다."""
    path = request.url.path
    if path.startswith("/api/") and path not in _AUTH_EXEMPT_API_PATHS:
        try:
            api_common.check_auth(request)
            api_common.check_csrf_for_unsafe_request(request)
            api_common.check_public_operation_allowed(request)
            api_common.check_role_for_request(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


# 실행 순서: request tracker -> auth -> foreground priority -> route
app.middleware("http")(foreground_priority_middleware)
app.middleware("http")(api_auth_middleware)
app.middleware("http")(request_tracker_middleware)
app.middleware("http")(public_host_middleware)


# 2. 정적 파일 및 템플릿 설정
# 현재 파일(web_main.py)의 위치를 기준으로 절대 경로 설정하여 실행 위치에 영향받지 않도록 함
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# 3. API 라우터 등록
app.include_router(web_api.router)

# 페이지 라우터 생성
page_router = APIRouter()


def _is_page_authorized(request: Request, ctx) -> bool:
    use_login = ctx.full_config.get("use_login", True)
    return not use_login or api_common.is_authenticated(request, ctx=ctx)


def _page_role(request: Request, ctx) -> str:
    if not ctx.full_config.get("use_login", True):
        return ADMIN
    claims = api_common.get_session_claims(request, ctx=ctx)
    return claims.role if claims is not None else ""


# 공통 페이지 렌더링 함수 (로그인 체크 포함)
async def render_page(
    request: Request,
    template_name: str,
    active_page: str,
    extra_context: dict = None,
    view_market: str = "common",
    required_role: str = VIEWER,
):
    try:
        ctx = web_api._get_ctx()
    except Exception:
        return templates.TemplateResponse(request, "login.html")

    if not _is_page_authorized(request, ctx):
        return templates.TemplateResponse(request, "login.html")

    user_role = _page_role(request, ctx)
    if not role_allows(user_role, required_role):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"authorization_error": "이 페이지에 접근할 권한이 부족합니다."},
            status_code=403,
        )

    claims = api_common.get_session_claims(request, ctx=ctx)
    context = {
        "active_page": active_page,
        "view_market": view_market,
        "username": claims.username if claims is not None else "",
        "user_role": user_role,
        "can_operate": role_allows(user_role, OPERATOR),
        "is_admin": role_allows(user_role, ADMIN),
    }
    if extra_context:
        context.update(extra_context)
    return templates.TemplateResponse(request, template_name, context)

# 4. 페이지 라우팅
@page_router.get("/")
async def index(request: Request):
    return await render_page(request, "index.html", "home")

@page_router.get("/stock")
async def stock(request: Request):
    # 종목 리스트는 클라이언트에서 /api/stocks/list + localStorage로 관리
    return await render_page(request, "stock.html", "stock", view_market="domestic")

@page_router.get("/balance")
async def balance(request: Request):
    initial_data = None
    try:
        from common.types import Exchange
        from view.web.api_common import _serialize_response
        ctx = web_api._get_ctx()
        if not _is_page_authorized(request, ctx):
            return await render_page(
                request,
                "balance.html",
                "balance",
                required_role=OPERATOR,
            )
        if not role_allows(_page_role(request, ctx), OPERATOR):
            return await render_page(
                request,
                "balance.html",
                "balance",
                required_role=OPERATOR,
            )
        resp = await ctx.stock_query_service.handle_get_account_balance(exchange=Exchange.KRX)
        result = _serialize_response(resp)
        # account_info 추가 (API 엔드포인트와 동일 로직)
        env = getattr(ctx, 'env', None) or getattr(getattr(ctx, 'broker', None), 'env', None)
        if env:
            config = getattr(env, 'active_config', None) or {}
            acc_no = (config.get("stock_account_number") or config.get("CANO") or
                      getattr(env, 'stock_account_number', None) or "번호없음")
            result['account_info'] = {
                "number": acc_no,
                "type": "모의투자" if getattr(env, 'is_paper_trading', False) else "실전투자",
                "exchange": "KRX",
            }
        from view.web.data_masking import mask_sensitive_data
        from view.web.deployment_policy import is_public_mode
        if is_public_mode(ctx):
            result = mask_sensitive_data(result)
        initial_data = result
    except Exception:
        pass
    extra = {"initial_data": initial_data} if initial_data else None
    return await render_page(
        request,
        "balance.html",
        "balance",
        extra_context=extra,
        view_market="domestic",
        required_role=OPERATOR,
    )

@page_router.get("/order")
async def order(request: Request):
    return await render_page(
        request,
        "order.html",
        "order",
        view_market="domestic",
        required_role=OPERATOR,
    )

@page_router.get("/overseas")
async def overseas(request: Request):
    return await render_page(request, "overseas.html", "overseas", view_market="overseas_us")

@page_router.get("/ranking")
async def ranking(request: Request):
    return await render_page(request, "ranking.html", "ranking", view_market="domestic")

@page_router.get("/marketcap")
async def marketcap(request: Request):
    return await render_page(request, "marketcap.html", "marketcap", view_market="domestic")

@page_router.get("/virtual")
async def virtual(request: Request):
    return await render_page(
        request,
        "virtual.html",
        "virtual",
        required_role=OPERATOR,
    )

@page_router.get("/scheduler")
async def scheduler(request: Request):
    return await render_page(
        request,
        "scheduler.html",
        "scheduler",
        required_role=OPERATOR,
    )

@page_router.get("/program")
async def program(request: Request):
    return await render_page(
        request,
        "program.html",
        "program",
        view_market="domestic",
        required_role=OPERATOR,
    )

@page_router.get("/system")
async def system(request: Request):
    return await render_page(
        request,
        "system.html",
        "system",
        required_role=OPERATOR,
    )

@page_router.get("/favorite")
async def favorite(request: Request):
    return await render_page(
        request,
        "favorite.html",
        "favorite",
        view_market="domestic",
        required_role=OPERATOR,
    )

@page_router.get("/operator")
async def operator_dashboard(request: Request):
    return await render_page(
        request,
        "operator_dashboard.html",
        "operator",
        required_role=OPERATOR,
    )

@page_router.get("/strategy-reports")
async def strategy_reports(request: Request):
    return await render_page(request, "strategy_reports.html", "strategy_reports")

# 5. 로그아웃
@page_router.get("/logout")
async def logout(request: Request):
    from fastapi.responses import RedirectResponse
    from view.web import security

    claims = api_common.get_session_claims(request)
    if claims is not None:
        security.revoke_session(claims.session_id)
    response = RedirectResponse(url="/")
    response.delete_cookie(security.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(security.CSRF_COOKIE_NAME, path="/")
    return response

app.include_router(page_router)
