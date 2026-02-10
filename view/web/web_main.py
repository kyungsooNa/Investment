"""
FastAPI 웹 서버 진입점.
실행: python -m uvicorn view.web.web_main:app --reload --host 0.0.0.0 --port 8000
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from view.web.web_app_initializer import WebAppContext
from view.web.web_api import router as api_router, set_context

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# 서비스 컨텍스트
ctx = WebAppContext()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 서비스 초기화."""
    ctx.load_config_and_env()
    success = await ctx.initialize_services(is_paper_trading=True)
    if success:
        set_context(ctx)
        print(f"[Web] 서비스 초기화 완료 ({ctx.get_env_type()})")
        print(f"[Web] http://localhost:8000 에서 접속 가능")
    else:
        print("[Web] 서비스 초기화 실패! API 엔드포인트가 동작하지 않을 수 있습니다.")
    yield


# FastAPI 앱
app = FastAPI(title="Investment Web View", lifespan=lifespan)

# 정적 파일 및 템플릿
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

# API 라우터
app.include_router(api_router)


@app.get("/")
async def index(request: Request):
    """메인 페이지."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "env_type": ctx.get_env_type(),
        "market_open": ctx.is_market_open(),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("view.web.web_main:app", host="0.0.0.0", port=8000, reload=True)
