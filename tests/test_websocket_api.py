# tests/test_websocket_api.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from api.websocket_api import WebSocketAPI

@pytest.mark.asyncio
async def test_websocket_api_initialization():
    # MagicMock을 사용하여 env.get_full_config() 가 필요한 설정을 반환하도록 설정
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://dummy-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://dummy-base-url"
    }

    # 테스트 대상 객체 생성
    ws_api = WebSocketAPI(env=mock_env)

    # 속성이 잘 초기화됐는지 검증
    assert ws_api._websocket_url == "wss://dummy-url"
    assert ws_api._rest_api_key == "dummy-api-key"
    assert ws_api._rest_api_secret == "dummy-secret-key"
    assert ws_api._base_rest_url == "https://dummy-base-url"
    assert ws_api._is_connected is False
    assert ws_api.ws is None

@pytest.mark.asyncio
async def test_websocket_api_connect_success():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://test-base"
    }

    ws_api = WebSocketAPI(env=mock_env)

    with patch("api.websocket_api.websockets.connect", new_callable=AsyncMock) as mock_connect, \
         patch.object(ws_api, "_get_approval_key", new_callable=AsyncMock, return_value="approval-key"):
        await ws_api.connect()

        # ✅ 여기를 실제 인자에 맞게 수정
        mock_connect.assert_called_once_with("wss://test-url", ping_interval=20, ping_timeout=20)
        assert ws_api._is_connected is True
        assert ws_api.approval_key == "approval-key"

def test_set_on_realtime_message_callback():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://test-base"
    }

    ws_api = WebSocketAPI(env=mock_env)

    # 콜백 함수 정의
    def dummy_callback(msg):
        return f"received: {msg}"

    ws_api.on_realtime_message_callback = dummy_callback

    # 설정된 콜백 확인
    assert ws_api.on_realtime_message_callback("테스트") == "received: 테스트"

@pytest.mark.asyncio
@patch("api.websocket_api.requests.post")
async def test_get_approval_key(mock_post):
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://test-base"
    }

    ws_api = WebSocketAPI(env=mock_env)

    # 응답 모킹
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "approval_key": "MOCKED_KEY"
    }

    approval_key = await ws_api._get_approval_key()  # ✅ await 추가
    assert approval_key == "MOCKED_KEY"
