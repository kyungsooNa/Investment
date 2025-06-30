# tests/test_korea_invest_websocket_api.py
import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from brokers.korea_investment.korea_invest_websocket_api import KereaInvestWebSocketAPI

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

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

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
    mock_logger = MagicMock()
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)

    patch_target = f"{KereaInvestWebSocketAPI.__module__}.websockets.connect"

    with patch(patch_target, new_callable=AsyncMock) as mock_connect, \
         patch.object(ws_api, "_get_approval_key", new_callable=AsyncMock, return_value="approval-key"):

        # ✅ 웹소켓 객체를 명확히 설정
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(return_value="0|mock message")  # ✅ 경고 방지 핵심
        mock_connect.return_value = mock_websocket

        await ws_api.connect()

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

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    # 콜백 함수 정의
    def dummy_callback(msg):
        return f"received: {msg}"

    ws_api.on_realtime_message_callback = dummy_callback

    # 설정된 콜백 확인
    assert ws_api.on_realtime_message_callback("테스트") == "received: 테스트"

@pytest.mark.asyncio
async def test_get_approval_key():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://test-base"
    }

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    # 동적 패치 대상 설정
    patch_target = f"{KereaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "approval_key": "MOCKED_KEY"
        }

        approval_key = await ws_api._get_approval_key()
        assert approval_key == "MOCKED_KEY"

@pytest.mark.asyncio
async def test_websocket_api_connect_failure_due_to_approval_key():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://test-base"
    }

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    # 🧩 동적 경로로 패치 대상 문자열 생성
    patch_target = f"{KereaInvestWebSocketAPI.__module__}.websockets.connect"

    with patch(patch_target, new_callable=AsyncMock) as mock_connect, \
         patch.object(ws_api, "_get_approval_key", new_callable=AsyncMock, return_value=None):
        with pytest.raises(Exception, match="approval_key 발급 실패"):
            await ws_api.connect()


@pytest.mark.asyncio
async def test_websocket_api_disconnect_calls_close():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://test-base"
    }

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입
    mock_ws = AsyncMock()
    ws_api.ws = mock_ws
    ws_api._is_connected = True

    await ws_api.disconnect()

    mock_ws.close.assert_called_once()
    assert ws_api._is_connected is False


@pytest.mark.asyncio
async def test_on_receive_without_callback_logs_warning(caplog):
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://test-base"
    }

    ws_api = KereaInvestWebSocketAPI(env=mock_env)  # ❌ logger 주입 제거
    dummy_message = json.dumps({"header": {}, "body": {}})

    with caplog.at_level("WARNING"):
        await ws_api._on_receive(dummy_message)
        assert "수신된 메시지를 처리할 콜백이 등록되지 않았습니다." in caplog.text

@pytest.mark.asyncio
async def test_on_receive_with_callback_called():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://test-base"
    }

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입
    callback = AsyncMock()
    ws_api.on_realtime_message_callback = callback

    dummy_message = json.dumps({
        "header": {"tr_id": "H0STCNT0"},
        "body": {"output": {"msg": "test"}}
    })

    await ws_api._on_receive(dummy_message)
    callback.assert_awaited_once()

@pytest.mark.asyncio
async def test_on_receive_with_callback_called_once():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://test-url",
        "api_key": "dummy-key",
        "api_secret_key": "dummy-secret",
        "base_url": "https://test-base"
    }

    mock_logger = MagicMock()  # ✅ 추가
    ws_api = KereaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    dummy_callback = AsyncMock()
    ws_api.on_realtime_message_callback = dummy_callback

    dummy_message = json.dumps({
        "header": {
            "tr_id": "H0IFCNI0"
        },
        "body": {
            "output": {"key": "value"}
        }
    })

    await ws_api._on_receive(dummy_message)

    dummy_callback.assert_awaited_once_with({
        "header": {
            "tr_id": "H0IFCNI0"
        },
        "body": {
            "output": {"key": "value"}
        }
    })
