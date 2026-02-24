import os
import stat
import tempfile
import shutil
import pytest
import time
import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock
from view.web import web_api
from view.web.web_main import page_router
from core.cache.cache_manager import CacheManager


@pytest.fixture(scope="session")
def test_cache_config():
    # test_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".cache"))
    test_base_dir = tempfile.mktemp()
    return {
        "cache": {
            "base_dir": test_base_dir,
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }


@pytest.fixture(scope="function")
def cache_manager(test_cache_config):
    return CacheManager(config=test_cache_config)


@pytest.fixture(autouse=True)
def clear_cache_files(test_cache_config):
    base_dir = test_cache_config["cache"]["base_dir"]

    def on_rm_error(func, path, _):
        try:
            os.chmod(path, stat.S_IWRITE)
            time.sleep(0.2)  # 여유시간
            func(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[삭제 실패] {path}: {e}")

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

    yield

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

pytest.fixture(autouse=True)
def fast_sleep(monkeypatch, request):
    # 특정 TC만 원래 sleep을 쓰고 싶으면: @pytest.mark.real_sleep
    if request.node.get_closest_marker("real_sleep"):
        return

    # sync sleep 제거
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)

    # async sleep 제거
    async def _fast_async_sleep(*a, **k): return None
    monkeypatch.setattr(asyncio, "sleep", _fast_async_sleep)


# --- Web API 관련 공통 Fixture ---

@pytest.fixture
def test_app():
    """테스트용 FastAPI 앱 (Web API 라우터 포함)"""
    app = FastAPI()
    app.include_router(web_api.router)
    app.include_router(page_router)
    return app

@pytest.fixture
def web_client(test_app):
    """FastAPI TestClient"""
    return TestClient(test_app)

@pytest.fixture
def mock_web_ctx():
    """WebAppContext Mock 객체 및 전역 주입"""
    ctx = MagicMock()
    
    # 기본 속성 설정
    ctx.is_market_open.return_value = True
    ctx.get_env_type.return_value = "모의투자"
    ctx.get_current_time_str.return_value = "2025-01-01 12:00:00"
    ctx.initialized = True
    
    # 하위 서비스 Mocking
    ctx.stock_query_service = AsyncMock()
    ctx.order_execution_service = AsyncMock()
    ctx.broker = AsyncMock()
    ctx.virtual_manager = MagicMock()
    ctx.scheduler = AsyncMock()
    
    # 환경 설정 Mocking
    ctx.env = MagicMock()
    ctx.env.active_config = {"auth": {"username": "admin", "password": "password", "secret_key": "secret"}}
    ctx.full_config = {"auth": {"username": "admin", "password": "password"}}
    
    # 전역 컨텍스트 설정
    web_api.set_ctx(ctx)
    return ctx
