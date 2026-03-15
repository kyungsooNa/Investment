import os
import stat
import tempfile
import shutil
import pytest
import pandas as pd
import httpx
import time
import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest import mock
from unittest.mock import MagicMock, AsyncMock
from core.cache.cache_manager import CacheManager

@pytest.fixture(autouse=True)
def mock_heavy_io(monkeypatch):
    """
    테스트 속도 저하를 유발하는 무거운 I/O 및 네트워크 리소스 정리 작업을 모킹합니다.
    """
    # 1. sqlite3.connect: 프로덕션 stock_code_list.db 로딩 방지
    #    테스트용 tmp_path DB는 통과시키고, 프로덕션 경로만 인메모리 DB로 대체
    import sqlite3
    _original_connect = sqlite3.connect
    _PROD_DB_PATTERN = os.path.join("data", "stock_code_list.db")
    def _create_mock_stock_db():
        conn = _original_connect(":memory:")
        conn.execute("CREATE TABLE stocks (종목코드 TEXT, 종목명 TEXT, 시장구분 TEXT, 상장주식수 INTEGER)")
        conn.executemany("INSERT INTO stocks VALUES (?, ?, ?, ?)", [
            ("005930", "삼성전자", "KOSPI", 1000),
            ("000660", "SK하이닉스", "KOSPI", 500),
        ])
        conn.commit()
        return conn
    def _mock_sqlite_connect(database, *args, **kwargs):
        if isinstance(database, str) and _PROD_DB_PATTERN in database:
            # pytest tmp 경로가 아닌 프로덕션 경로만 인터셉트
            if "pytest" not in database and "Temp" not in database:
                return _create_mock_stock_db()
        return _original_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", _mock_sqlite_connect)

    # 2. httpx.AsyncClient.aclose: 세션 종료 시 불필요한 대기 제거
    async def _mock_aclose(*args, **kwargs):
        return None
    monkeypatch.setattr(httpx.AsyncClient, "aclose", _mock_aclose)

# --- Web API 관련 공통 Fixture ---


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

@pytest.fixture(autouse=True)
def fast_sleep(request):
    # 특정 TC만 원래 sleep을 쓰고 싶으면: @pytest.mark.real_sleep
    if request.node.get_closest_marker("real_sleep"):
        yield
        return

    # unittest.TestCase 호환성을 위해 mock.patch 사용
    with mock.patch("time.sleep"), mock.patch("asyncio.sleep", new_callable=AsyncMock):
        yield


# --- Web API 관련 공통 Fixture ---

@pytest.fixture(scope="session")
def test_app():
    """테스트용 FastAPI 앱 (Web API 라우터 포함)"""
    from view.web import web_api
    from view.web.web_main import page_router
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
    from view.web import web_api
    ctx = MagicMock()
    
    # 기본 속성 설정
    ctx.is_market_open_now = AsyncMock(return_value=True)
    ctx.get_env_type.return_value = "모의투자"
    ctx.get_current_time_str.return_value = "2025-01-01 12:00:00"
    ctx.initialized = True
    
    # 하위 서비스 Mocking
    ctx.stock_query_service = AsyncMock()
    ctx.order_execution_service = AsyncMock()
    ctx.broker = AsyncMock()
    ctx.virtual_manager = MagicMock()
    # [Fix] get_trade_amount가 연산에 사용되므로 float 반환 설정 (TypeError 방지)
    ctx.virtual_manager.get_trade_amount.side_effect = lambda p, q=1, **kwargs: float(p * q)
    ctx.virtual_manager.calculate_return.return_value = 0.0
    
    # scheduler는 동기/비동기 메서드가 혼재하므로 MagicMock 기반에 비동기 메서드만 AsyncMock 할당
    ctx.scheduler = MagicMock()
    ctx.scheduler.start = AsyncMock()
    ctx.scheduler.stop = AsyncMock()
    ctx.scheduler.start_strategy = AsyncMock()
    
    # 환경 설정 Mocking
    ctx.env = MagicMock()
    ctx.env.active_config = {"auth": {"username": "admin", "password": "password", "secret_key": "secret"}}
    ctx.full_config = {"auth": {"username": "admin", "password": "password"}}
    
    # 알림 매니저 Mocking
    ctx.notification_manager = MagicMock()
    ctx.notification_manager.emit = AsyncMock()

    # 전역 컨텍스트 설정
    web_api.set_ctx(ctx)
    return ctx
