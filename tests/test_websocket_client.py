import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_websocket_client import KoreaInvestWebSocketClient  # 실제 경로에 맞게 조정

@pytest.mark.asyncio
async def test_websocket_client_initialization():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://mock-url",
        "api_key": "mock-api-key",
        "api_secret_key": "mock-secret",
        "base_url": "https://mock-base"
    }

    client = KoreaInvestWebSocketClient(env=mock_env)
    assert client._env == mock_env
    assert client._config["websocket_url"] == "wss://mock-url"
    assert client._is_connected is False

@pytest.mark.asyncio
async def test_websocket_client_connect_success():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://mock-url",
        "api_key": "mock-api-key",
        "api_secret_key": "mock-secret",
        "base_url": "https://mock-base"
    }

    client = KoreaInvestWebSocketClient(env=mock_env)
    client.approval_key = "MOCKED_KEY"

    patch_target = f"{KoreaInvestWebSocketClient.__module__}.websockets.connect"
    with patch(patch_target, new_callable=AsyncMock) as mock_connect:
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(return_value="0|mock-message")  # ✅ 이 줄이 경고 방지 핵심
        mock_connect.return_value = mock_websocket

        await client.connect()

        mock_connect.assert_called_once_with("wss://mock-url", ping_interval=20, ping_timeout=20)
        assert client._is_connected is True


@pytest.mark.asyncio
async def test_websocket_client_disconnect():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://mock-url",
        "api_key": "mock-api-key",
        "api_secret_key": "mock-secret",
        "base_url": "https://mock-base"
    }

    client = KoreaInvestWebSocketClient(env=mock_env)
    mock_ws = AsyncMock()
    client.ws = mock_ws
    client._is_connected = True

    await client.disconnect()
    mock_ws.close.assert_awaited_once()
    assert client._is_connected is False


@pytest.mark.asyncio
async def test_receive_message_handler_called():
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://mock-url",
        "api_key": "mock-api-key",
        "api_secret_key": "mock-secret",
        "base_url": "https://mock-base"
    }

    client = KoreaInvestWebSocketClient(env=mock_env)
    dummy_message = '{"header": {}, "body": {}}'
    callback = AsyncMock()
    client.on_realtime_message_callback = callback

    # _on_receive가 정의되어 있다면 아래 실행 가능
    await client._on_receive(dummy_message)
    callback.assert_awaited_once()
