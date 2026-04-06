import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
import view.web.api_common as api_common
from view.web.web_main import app, lifespan, _DebugHandler, _start_debug_server, _needs_foreground

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

def test_debug_handler_do_get_404():
    """진단 서버 404 경로 처리 테스트"""
    handler = _DebugHandler.__new__(_DebugHandler)
    handler.path = "/invalid"
    handler.send_response = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_once_with(404)

def test_debug_handler_do_get_200():
    """진단 서버 정상 요청 및 데이터 직렬화 테스트"""
    handler = _DebugHandler.__new__(_DebugHandler)
    handler.path = "/debug/requests"
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    
    api_common._active_requests = {"test_req": {"path": "/api/test", "method": "GET", "start": time.monotonic(), "query": "a=1"}}
    api_common._recent_completed.append({"path": "/api/test2", "elapsed_sec": 0.1, "at": 100.0})
    
    handler.do_GET()
    
    handler.send_response.assert_called_once_with(200)
    handler.wfile.write.assert_called_once()
    
    api_common._active_requests.clear()
    api_common._recent_completed.clear()

def test_debug_handler_log_message():
    """진단 서버 로그 억제 함수 동작 확인"""
    handler = _DebugHandler.__new__(_DebugHandler)
    handler.log_message("format", "arg")

@patch("view.web.web_main.ThreadingHTTPServer")
def test_start_debug_server_oserror(mock_server, capsys):
    """진단 서버 포트 충돌 등 OSError 발생 시 로그 출력 확인"""
    mock_server.side_effect = OSError("Address already in use")
    _start_debug_server()
    captured = capsys.readouterr()
    assert "진단 서버 시작 실패" in captured.out

def test_request_tracker_middleware():
    """요청 추적 미들웨어의 활성 요청 등록 및 완료 이력 저장 테스트"""
    api_common._recent_completed.clear()
    with TestClient(app) as client:
        client.get("/docs") 
        initial_len = len(api_common._recent_completed)
        
        client.get("/api/dummy_test_route?param=1")
        
        assert len(api_common._recent_completed) == initial_len + 1
        assert api_common._recent_completed[-1]["path"] == "/api/dummy_test_route"

        original_max = api_common._RECENT_MAX
        try:
            api_common._RECENT_MAX = 2
            client.get("/api/dummy_test_route1")
            client.get("/api/dummy_test_route2")
            client.get("/api/dummy_test_route3")
            assert len(api_common._recent_completed) == 2
            assert api_common._recent_completed[-1]["path"] == "/api/dummy_test_route3"
        finally:
            api_common._RECENT_MAX = original_max

def test_needs_foreground():
    """Foreground 적용 대상 경로 판단 테스트"""
    assert _needs_foreground("/api/stock/123") is True
    assert _needs_foreground("/api/order") is True
    assert _needs_foreground("/api/ranking/progress") is False
    assert _needs_foreground("/api/stock/search") is False
    assert _needs_foreground("/api/system") is False

def test_foreground_priority_middleware():
    """Foreground 우선순위 미들웨어 동작(context 진입) 확인"""
    mock_ctx = MagicMock()
    mock_fg = MagicMock()
    mock_ctx.foreground_scheduler = mock_fg
    
    with patch("view.web.web_main.api_common._ctx", mock_ctx):
        with TestClient(app) as client:
            client.get("/api/stock/123")
            mock_fg.context.assert_called()

def test_all_page_routers():
    """모든 페이지 라우터들이 200 정상 응답을 하는지 테스트"""
    mock_ctx = MagicMock()
    mock_ctx.full_config = {
        "use_login": True,
        "auth": {"secret_key": "correct_token"}
    }
    
    with patch("view.web.web_api._get_ctx", return_value=mock_ctx):
        with TestClient(app) as client:
            client.cookies.set("access_token", "correct_token")
            pages = ["/stock", "/balance", "/order", "/ranking", "/marketcap", "/virtual", "/scheduler", "/program", "/system"]
            for page in pages:
                response = client.get(page)
                assert response.status_code == 200
                assert "Investment Login" not in response.text

def test_logout():
    """로그아웃 시 리다이렉트와 쿠키 삭제 처리 테스트"""
    with TestClient(app) as client:
        response = client.get("/logout", follow_redirects=False)
        assert response.status_code == 307
        assert "access_token" in response.headers.get("set-cookie", "")
