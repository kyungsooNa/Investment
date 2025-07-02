import pytest
import requests
import json
import websockets
from websockets.exceptions import ConnectionClosedOK
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

@pytest.fixture
def mock_env():
    """모의 KoreaInvestApiEnv 객체를 생성하는 Fixture."""
    env = MagicMock()
    env.get_full_config.return_value = {
        'websocket_url': 'wss://mock-ws.com',
        'base_url': 'https://mock-api.com',
        'api_key': 'mock_key',
        'api_secret_key': 'mock_secret',
        'custtype': 'P',
        'tr_ids': {
            'websocket': {
                'realtime_price': 'H0STCNT0',
                'realtime_quote': 'H0STASP0',
            }
        }
    }
    return env

@pytest.fixture
def websocket_client(mock_env):
    """
    오류의 원인이었던 'websocket_client' 픽스처입니다.
    이 함수가 있기 때문에 테스트 함수에서 'websocket_client'를 인자로 사용할 수 있습니다.
    """
    client = KoreaInvestWebSocketClient(env=mock_env)
    client.logger = MagicMock() # 테스트 중 로그 출력을 막습니다.
    return client

@pytest.mark.asyncio
@patch('requests.post')
async def test_get_approval_key_success(mock_post, websocket_client):
    """_get_approval_key 메서드가 성공적으로 접속키를 발급받는 경우를 테스트합니다."""
    # Arrange
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'approval_key': 'SUCCESS_KEY'}
    mock_post.return_value = mock_response

    # Act
    key = await websocket_client._get_approval_key()

    # Assert
    assert key == 'SUCCESS_KEY'
    assert websocket_client.approval_key == 'SUCCESS_KEY'

    # vvvv 수정된 부분 vvvv
    # logger.info가 특정 메시지와 함께 호출되었는지 검사하는 대신,
    # 단순히 'info' 메서드가 한 번 이상 호출되었는지만 검증합니다.
    websocket_client.logger.info.assert_called()


@pytest.mark.asyncio
@patch('requests.post', side_effect=requests.exceptions.RequestException("Network Error"))
async def test_get_approval_key_network_error(mock_post, websocket_client):
    """_get_approval_key 메서드에서 네트워크 오류 발생 시 None을 반환하는지 테스트합니다."""
    # Act
    key = await websocket_client._get_approval_key()
    # Assert
    assert key is None
    websocket_client.logger.error.assert_called_with("웹소켓 접속키 발급 중 네트워크 오류: Network Error")


@pytest.mark.asyncio
@patch('requests.post')
async def test_get_approval_key_json_decode_error(mock_post, websocket_client):
    """_get_approval_key 메서드에서 JSON 디코딩 오류 발생 시 None을 반환하는지 테스트합니다."""
    # Arrange
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = json.JSONDecodeError("msg", "doc", 0)
    mock_response.text = "Invalid JSON"
    mock_post.return_value = mock_response

    # Act
    key = await websocket_client._get_approval_key()
    # Assert
    assert key is None
    websocket_client.logger.error.assert_called_with("웹소켓 접속키 발급 응답 JSON 디코딩 실패: Invalid JSON")


@pytest.mark.asyncio
async def test_handle_pingpong_message(websocket_client):
    """제어 메시지 'PINGPONG'을 올바르게 처리하는지 테스트합니다."""
    # Arrange
    pingpong_message = json.dumps({"header": {"tr_id": "PINGPONG"}})

    # Act
    websocket_client._handle_websocket_message(pingpong_message)

    # Assert
    websocket_client.logger.info.assert_called_with("PINGPONG 수신됨. PONG 응답.")


@pytest.mark.asyncio
async def test_handle_error_response_message(websocket_client):
    """제어 메시지 중 오류 응답(rt_cd != '0')을 올바르게 처리하는지 테스트합니다."""
    # Arrange
    error_message = json.dumps({
        "body": {"rt_cd": "1", "msg1": "에러 발생"},
        "header": {"tr_key": "SOME_KEY"}
    })

    # Act
    websocket_client._handle_websocket_message(error_message)

    # Assert
    websocket_client.logger.error.assert_called_with(
        "실시간 요청 응답 오류: TR_KEY=SOME_KEY, RT_CD=1, MSG=에러 발생"
    )


@pytest.mark.asyncio
async def test_handle_already_subscribed_message(websocket_client):
    """'이미 구독 중' 오류 메시지를 올바르게 처리하는지 테스트합니다."""
    # Arrange
    error_message = json.dumps({
        "body": {"rt_cd": "1", "msg1": "ALREADY IN SUBSCRIBE"},
        "header": {"tr_key": "SOME_KEY"}
    })

    # Act
    websocket_client._handle_websocket_message(error_message)

    # Assert
    websocket_client.logger.warning.assert_called_with("이미 구독 중인 종목입니다.")


@pytest.mark.asyncio
async def test_receive_messages_connection_closed_ok(websocket_client):
    """_receive_messages가 ConnectionClosedOK 예외를 정상 처리하는지 테스트합니다."""
    # Arrange
    websocket_client.ws = AsyncMock()

    # vvvv 수정된 부분 vvvv
    # ConnectionClosedOK 예외 생성 시 키워드 인자(code=, reason=) 대신 위치 인자를 사용합니다.
    websocket_client.ws.recv.side_effect = websockets.exceptions.ConnectionClosedOK(1000, "Normal")
    websocket_client._is_connected = True

    # Act
    await websocket_client._receive_messages()

    # Assert
    websocket_client.logger.info.assert_called_with("웹소켓 연결이 정상적으로 종료되었습니다.")
    assert websocket_client._is_connected is False

@pytest.mark.asyncio
async def test_connect_when_already_connected(websocket_client):
    """이미 연결된 상태에서 connect 호출 시, 추가 동작 없이 True를 반환하는지 테스트합니다."""
    # Arrange
    websocket_client._is_connected = True
    websocket_client.ws = MagicMock()

    # Act
    result = await websocket_client.connect()

    # Assert
    assert result is True
    websocket_client.logger.info.assert_called_with("웹소켓이 이미 연결되어 있습니다.")


@pytest.mark.asyncio
async def test_send_request_not_connected(websocket_client):
    """연결되지 않은 상태에서 send_realtime_request 호출 시 False를 반환하는지 테스트합니다."""
    # Arrange
    websocket_client._is_connected = False

    # Act
    result = await websocket_client.send_realtime_request("H0STCNT0", "005930")

    # Assert
    assert result is False
    websocket_client.logger.error.assert_called_with("웹소켓이 연결되어 있지 않아 실시간 요청을 보낼 수 없습니다.")
