import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from view.web.web_main import app, lifespan

# --- Fixtures ---
@pytest.fixture
def mock_web_app_context_cls():
    """WebAppContext 클래스를 Mocking하여 실제 초기화를 방지"""
    with patch("view.web.web_main.WebAppContext") as MockClass:
        # Mock 인스턴스 설정
        mock_instance = MockClass.return_value
        mock_instance.load_config_and_env = MagicMock()
        mock_instance.initialize_services = AsyncMock(return_value=True)
        mock_instance.initialize_scheduler = MagicMock()
        mock_instance.scheduler = MagicMock()
        mock_instance.scheduler.restore_state = AsyncMock()
        mock_instance.scheduler._running = True
        mock_instance.scheduler.stop = AsyncMock()
        mock_instance.start_background_tasks = MagicMock()
        mock_instance.shutdown = AsyncMock()
        yield MockClass

@pytest.fixture
def mock_web_api_module():
    """web_api 모듈을 Mocking"""
    with patch("view.web.web_main.web_api") as mock_module:
        yield mock_module

# --- Tests ---

@pytest.mark.asyncio
async def test_lifespan_startup_shutdown(mock_web_app_context_cls, mock_web_api_module):
    """lifespan의 시작 및 종료 로직 테스트"""
    mock_ctx = mock_web_app_context_cls.return_value
    
    # Run lifespan
    async with lifespan(app):
        # Startup checks
        mock_web_app_context_cls.assert_called_once()
        mock_ctx.load_config_and_env.assert_called_once()
        mock_ctx.initialize_services.assert_awaited_once_with(is_paper_trading=True)
        mock_web_api_module.set_ctx.assert_called_once_with(mock_ctx)
        mock_ctx.initialize_scheduler.assert_called_once()
        mock_ctx.scheduler.restore_state.assert_awaited_once()
        mock_ctx.start_background_tasks.assert_called_once()
    
    # Shutdown checks
    mock_ctx.shutdown.assert_awaited_once()
    mock_ctx.scheduler.stop.assert_awaited_once_with(save_state=True)

@pytest.mark.asyncio
async def test_lifespan_shutdown_scheduler_not_running(mock_web_app_context_cls, mock_web_api_module):
    """스케줄러가 실행 중이 아닐 때 shutdown 시 stop 호출 안함 테스트"""
    mock_ctx = mock_web_app_context_cls.return_value
    mock_ctx.scheduler._running = False # Not running

    async with lifespan(app):
        pass
    
    # Shutdown checks
    mock_ctx.shutdown.assert_awaited_once()
    mock_ctx.scheduler.stop.assert_not_awaited()

def test_render_page_ctx_not_initialized(mock_web_app_context_cls):
    """_get_ctx 실패(초기화 전) 시 로그인 페이지 렌더링 테스트"""
    # web_api._get_ctx가 예외를 던지도록 설정하여 render_page의 except 블록 테스트
    with patch("view.web.web_api._get_ctx", side_effect=Exception("Not initialized")):
        with TestClient(app) as client:
            response = client.get("/")
            
            assert response.status_code == 200
            # 로그인 페이지로 폴백되었는지 확인
            assert "Investment Login" in response.text

def test_render_page_invalid_token(mock_web_app_context_cls):
    """토큰 불일치 시 로그인 페이지 렌더링 테스트"""
    # Mock Context 설정
    mock_ctx = MagicMock()
    mock_ctx.full_config = {
        "use_login": True,
        "auth": {"secret_key": "correct_token"}
    }
    
    # _get_ctx가 mock_ctx를 반환하도록 설정
    with patch("view.web.web_api._get_ctx", return_value=mock_ctx):
        with TestClient(app) as client:
            # 잘못된 토큰 설정
            client.cookies.set("access_token", "wrong_token")
            
            response = client.get("/")
            
            assert response.status_code == 200
            assert "Investment Login" in response.text

def test_render_page_success(mock_web_app_context_cls):
    """정상 토큰으로 페이지 렌더링 성공 테스트"""
    mock_ctx = MagicMock()
    mock_ctx.full_config = {
        "use_login": True,
        "auth": {"secret_key": "correct_token"}
    }
    
    with patch("view.web.web_api._get_ctx", return_value=mock_ctx):
        with TestClient(app) as client:
            client.cookies.set("access_token", "correct_token")
            
            response = client.get("/")
            
            assert response.status_code == 200
            assert "Investment Login" not in response.text
            assert "Investment - Web View" in response.text
