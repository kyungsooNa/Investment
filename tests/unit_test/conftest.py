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
from view.web import web_api
from view.web.web_main import page_router
from core.cache.cache_manager import CacheManager

@pytest.fixture(autouse=True)
def mock_heavy_io(monkeypatch):
    """
    테스트 속도 저하를 유발하는 무거운 I/O 및 네트워크 리소스 정리 작업을 모킹합니다.
    """
    # 1. pandas.read_csv: 대용량 주식 코드 리스트 로딩 방지
    original_read_csv = pd.read_csv
    def _mock_read_csv(*args, **kwargs):
        # stock_code_list.csv 파일 로딩 시 테스트용 가벼운 데이터 반환
        if args and isinstance(args[0], str) and 'stock_code_list.csv' in args[0]:
            return pd.DataFrame({
                "종목코드": ["005930", "000660"],
                "종목명": ["삼성전자", "SK하이닉스"],
                "시장구분": ["KOSPI", "KOSPI"],
                "상장주식수": [1000, 500]
            })
        return original_read_csv(*args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", _mock_read_csv)

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
