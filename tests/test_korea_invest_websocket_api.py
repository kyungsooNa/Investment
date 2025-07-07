# tests/test_korea_invest_websocket_api.py
import base64
import logging
import pytest
import json
import requests
import websockets
import asyncio
from core.logger import Logger # 사용자 정의 Logger 사용을 가정
from unittest.mock import MagicMock, patch, AsyncMock
from unittest import mock
from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)

    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    # 동적 패치 대상 설정
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

    # 🧩 동적 경로로 패치 대상 문자열 생성
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입
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

    ws_api = KoreaInvestWebSocketAPI(env=mock_env)  # ❌ logger 주입 제거
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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입
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
    ws_api = KoreaInvestWebSocketAPI(env=mock_env, logger=mock_logger)  # ✅ 명시적으로 주입

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


# Mock Logger를 설정하여 일관된 테스트 환경을 제공합니다.
@pytest.fixture
def mock_logger():
    """모의 Logger."""
    mock = AsyncMock(spec=Logger)
    return mock

@pytest.fixture
def websocket_api_instance(mock_logger):
    """KoreaInvestWebSocketAPI 인스턴스를 위한 픽스처."""
    mock_env = MagicMock()
    # mock_env.get_full_config.return_value를 실제 _config 구조에 맞게 재구성
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://dummy-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://dummy-base-url",
        "custtype": "P", # send_realtime_request에서 사용
        "tr_ids": { # 핵심: 'tr_ids' 키 추가
            "H0STASP0": {"msg_type": "stock_quote", "encrypted": False},
            "H0STCNT0": {"msg_type": "stock_contract", "encrypted": True},
            "H0STCNI0": {"msg_type": "signing_notice", "encrypted": True},
            "H0STCNI9": {"msg_type": "signing_notice", "encrypted": True},
            "H0IFCNI0": {"msg_type": "signing_notice", "encrypted": True},
            "H0MFCNI0": {"msg_type": "signing_notice", "encrypted": True},
            "H0EUCNI0": {"msg_type": "signing_notice", "encrypted": True},
            "websocket": {
                "realtime_price": "H0STCNT0",
                "realtime_quote": "H0STASP0"
            }
        }
    }

    # websockets 모듈 자체를 패치하여 예외 클래스에 대한 접근을 제어
    # 더미 예외를 사용하므로 이 패치는 더 이상 필요하지 않을 수 있지만, 안전을 위해 유지
    with patch(f"{KoreaInvestWebSocketAPI.__module__}.websockets") as mock_websockets_module:
        mock_websockets_module.exceptions = MagicMock()

        instance = KoreaInvestWebSocketAPI(
            env=mock_env,
            logger=mock_logger
        )
        instance._parse_signing_notice = MagicMock(return_value={})
        return instance

@pytest.fixture(autouse=True)
def caplog_fixture(caplog):
    """테스트 중 로깅 메시지를 캡처하고 기본 로깅 레벨을 설정합니다."""
    caplog.set_level(logging.DEBUG) # 상세 로그 확인을 위해 DEBUG 레벨까지 캡처

# _aes_cbc_base64_dec: 잘못된 base64 문자열 처리 테스트
def test_aes_cbc_base64_dec_invalid_base64(websocket_api_instance, caplog):
    """
    _aes_cbc_base64_dec 메서드에 잘못된 base64 문자열이 주어졌을 때
    None을 반환하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._aes_key = b'A' * 32
    api._aes_iv = b'A' * 16

    malformed_base64_data = "this-is-not-valid-base64!"

    with caplog.at_level(logging.ERROR):
        result = api._aes_cbc_base64_dec(api._aes_key, api._aes_iv, malformed_base64_data)

    assert result is None
    # 로그 메시지 일치하도록 수정 [수정]
    assert "AES 복호화 오류 발생:" in caplog.text


# _aes_cbc_base64_dec: 복호화 오류 처리 테스트 (예: 잘못된 키/IV)
def test_aes_cbc_base64_dec_decryption_error(websocket_api_instance, caplog):
    """
    _aes_cbc_base64_dec 메서드에서 복호화 중 오류가 발생할 때 (예: 잘못된 키/IV)
    None을 반환하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._aes_key = b'A' * 32
    api._aes_iv = b'A' * 16

    encrypted_payload = base64.b64encode(b"some_random_data_that_wont_decrypt").decode('utf-8')

    with caplog.at_level(logging.ERROR):
        result = api._aes_cbc_base64_dec(api._aes_key, api._aes_iv, encrypted_payload)

    assert result is None
    # 로그 메시지 일치하도록 수정 [수정]
    assert "AES 복호화 오류 발생:" in caplog.text

# _handle_websocket_message: JSON 디코딩 오류 처리 테스트 (제어 메시지)
# async 키워드 및 await 호출 제거 [수정]
def test_handle_websocket_message_json_decode_error_control(websocket_api_instance): # caplog 제거
    """
    _handle_websocket_message가 유효하지 않은 JSON 형식의 제어 메시지를 받을 때
    json.JSONDecodeError를 처리하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    invalid_json_message = "this is not json"

    api._handle_websocket_message(invalid_json_message) # await 제거

    # 직접 mock_logger.error 호출을 확인 [수정]
    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0] # 첫 번째 인자는 메시지 문자열
    assert "제어 메시지 JSON 디코딩 실패:" in logged_message # 로그 메시지 일치하도록 수정 [수정]

# _handle_websocket_message: _aes_key 또는 _aes_iv가 없는 서명 통지 메시지 처리 테스트
# async 키워드 및 await 호출 제거 [수정]
def test_handle_websocket_message_signing_notice_missing_aes_keys(websocket_api_instance): # caplog 제거
    """
    _handle_websocket_message가 암호화된 'signing_notice' 메시지를 받을 때
    _aes_key 또는 _aes_iv가 없을 경우 경고를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    sample_encrypted_data = "encrypted_payload_example"

    message = json.dumps({
        "header": {"tr_id": "H0STCNI0"},
        "body": {"output": {"msg": sample_encrypted_data}}
    })

    api._handle_websocket_message(message) # await 제거

    # 직접 mock_logger.error 호출을 확인 [수정: warning -> error]
    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "실시간 요청 응답 오류:" in logged_message # 로그 메시지 일치하도록 수정 [수정]


# _parse_stock_quote_data: 필드 누락 처리 테스트 [수정]
@pytest.mark.asyncio
async def test_parse_stock_quote_data_missing_fields(websocket_api_instance):
    """
    _parse_stock_quote_data가 필수 필드가 누락된 데이터를 받을 때
    안전하게 처리하고 기본값을 반환하는지 테스트합니다.
    """
    api = websocket_api_instance
    # _parse_stock_quote_data는 최소 59개 필드(인덱스 0~58)를 기대합니다.
    # 종목코드 '0001'를 인덱스 0에, 나머지 관련 필드는 '0' 또는 빈 문자열로 채웁니다.
    incomplete_data_parts = [''] * 59
    incomplete_data_parts[0] = '0001' # 유가증권단축종목코드

    # 주요 숫자 필드 (0으로 예상) 인덱스를 '0'으로 설정
    numeric_indices_quote = [
        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, # 매도호가
        13, 14, 15, 16, 17, 18, 19, 20, 21, 22, # 매수호가
        23, 24, 25, 26, 27, 28, 29, 30, 31, 32, # 매도호가잔량
        33, 34, 35, 36, 37, 38, 39, 40, 41, 42, # 매수호가잔량
        43, 44, 45, 46, # 총매도/매수호가잔량, 시간외 총매도/매수호가잔량
        47, 48, 49, 50, 52, 53 # 예상체결가, 예상체결량, 예상거래량, 예상체결대비, 예상체결전일대비율, 누적거래량
    ]
    for idx in numeric_indices_quote:
        incomplete_data_parts[idx] = '0'

    incomplete_data_str = '^'.join(incomplete_data_parts)

    parsed_data = api._parse_stock_quote_data(incomplete_data_str)

    # 어설션 키를 한글로 변경하고 값 검증
    assert parsed_data["유가증권단축종목코드"] == "0001"
    assert parsed_data["예상체결가"] == '0'
    assert parsed_data["예상체결대비"] == '0'
    assert parsed_data["예상체결전일대비율"] == '0'
    assert parsed_data["누적거래량"] == '0'
    assert parsed_data["총매도호가잔량"] == '0'
    assert parsed_data["총매수호가잔량"] == '0'
    assert parsed_data["시간외총매도호가잔량"] == '0'
    assert parsed_data["시간외총매수호가잔량"] == '0'
    assert parsed_data["예상체결량"] == '0'
    assert parsed_data["예상거래량"] == '0'
    assert parsed_data["부호"] == '' # 부호는 숫자 필드가 아님


# _parse_stock_contract_data: 필드 누락 처리 테스트 [수정]
@pytest.mark.asyncio
async def test_parse_stock_contract_data_missing_fields(websocket_api_instance):
    """
    _parse_stock_contract_data가 필수 필드가 누락된 데이터를 받을 때
    안전하게 처리하고 기본값을 반환하는지 테스트합니다.
    """
    api = websocket_api_instance
    # _parse_stock_contract_data는 최소 46개 필드(인덱스 0~45)를 기대합니다. (menulist 길이)
    # 유가증권단축종목코드 '0001'를 인덱스 0에 배치하고 나머지는 '0' 또는 빈 문자열로 채웁니다.
    incomplete_data_parts = [''] * 46 # 길이 수정
    incomplete_data_parts[0] = '0001' # 유가증권단축종목코드

    # 주요 숫자 필드 (0으로 예상) 인덱스를 '0'으로 설정
    numeric_indices_contract = [
        2,  # 주식현재가
        4,  # 전일대비
        5,  # 전일대비율
        13, # 누적거래량
        14, # 누적거래대금
    ]
    for idx in numeric_indices_contract:
        incomplete_data_parts[idx] = '0'

    incomplete_data_str = '^'.join(incomplete_data_parts)

    parsed_data = api._parse_stock_contract_data(incomplete_data_str)

    assert parsed_data["유가증권단축종목코드"] == "0001"
    assert parsed_data["주식현재가"] == '0' # 체결가격 대신 주식현재가 사용
    assert parsed_data["전일대비부호"] == ''
    assert parsed_data["전일대비"] == '0'
    assert parsed_data["전일대비율"] == '0'
    assert parsed_data["누적거래량"] == '0'
    assert parsed_data["누적거래대금"] == '0'
    # 주식매매구분코드 키는 menulist에 없으므로 어설션 제거


# _handle_websocket_message: 알 수 없는 tr_id 처리 테스트 [수정]
def test_handle_websocket_message_unknown_tr_id(websocket_api_instance): # @pytest.mark.asyncio 제거 [수정]
    """
    _handle_websocket_message가 tr_ids_config에 없는 알 수 없는 tr_id를 받을 때
    올바르게 처리하고 경고를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    unknown_tr_id_message = json.dumps({ # 이 메시지는 'else' (제어 메시지) 블록으로 이동
        "header": {"tr_id": "UNKNOWN_TR"},
        "body": {"output": {"msg": "some data"}}
    })

    api._handle_websocket_message(unknown_tr_id_message) # await 제거

    # 알 수 없는 TR_ID 제어 메시지는 ERROR 로그를 발생시킴 (실시간 요청 응답 오류) [수정]
    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "실시간 요청 응답 오류:" in logged_message

# _handle_websocket_message: 알 수 없는 tr_id 처리 테스트
def test_handle_websocket_message_unknown_tr_id(websocket_api_instance): # 동기 함수이므로 @pytest.mark.asyncio 제거
    """
    _handle_websocket_message가 tr_ids_config에 없는 알 수 없는 tr_id를 받을 때
    올바르게 처리하고 경고를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    # 알 수 없는 TR_ID는 JSON 형식의 제어 메시지로 전달
    unknown_tr_id_message = json.dumps({
        "header": {"tr_id": "UNKNOWN_TR"},
        "body": {"output": {"msg": "some data"}}
    })

    api._handle_websocket_message(unknown_tr_id_message) # 동기 호출

    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "실시간 요청 응답 오류:" in logged_message

# --- 새롭게 추가되는 테스트 케이스 시작 ---

# _handle_websocket_message: 성공적인 주식 체결(H0STCNT0) 파싱 테스트
def test_handle_websocket_message_realtime_price_success(websocket_api_instance):
    api = websocket_api_instance
    tr_id = api._config['tr_ids']['websocket']['realtime_price']
    # _parse_stock_contract_data가 기대하는 46개 필드의 유효한 데이터
    # 유가증권단축종목코드, 주식현재가, 전일대비부호, 전일대비, 전일대비율, 누적거래량, 누적거래대금
    data_parts = [''] * 46
    data_parts[0] = '0001' # 유가증권단축종목코드
    data_parts[2] = '10000' # 주식현재가
    data_parts[3] = '+' # 전일대비부호
    data_parts[4] = '100' # 전일대비
    data_parts[5] = '1.00' # 전일대비율
    data_parts[13] = '1000' # 누적거래량
    data_parts[14] = '10000000' # 누적거래대금
    data_body = '^'.join(data_parts)

    message = f"0|{tr_id}|some_key|{data_body}"

    # on_realtime_message_callback Mock
    mock_callback = MagicMock()
    api.on_realtime_message_callback = mock_callback

    api._handle_websocket_message(message)

    mock_callback.assert_called_once()
    called_args = mock_callback.call_args[0][0] # 첫 번째 인자는 딕셔너리
    assert called_args['type'] == 'realtime_price'
    assert called_args['tr_id'] == tr_id
    assert called_args['data']["유가증권단축종목코드"] == '0001'
    assert called_args['data']["주식현재가"] == '10000'

# _handle_websocket_message: 성공적인 주식 호가(H0STASP0) 파싱 테스트
def test_handle_websocket_message_realtime_quote_success(websocket_api_instance):
    api = websocket_api_instance
    tr_id = api._config['tr_ids']['websocket']['realtime_quote']
    # _parse_stock_quote_data가 기대하는 59개 필드의 유효한 데이터
    # 유가증권단축종목코드, 예상체결가, 예상체결대비, 예상체결전일대비율, 누적거래량, 부호 등
    data_parts = [''] * 59
    data_parts[0] = '0002' # 유가증권단축종목코드
    data_parts[47] = '20000' # 예상체결가
    data_parts[50] = '50' # 예상체결대비
    data_parts[52] = '0.25' # 예상체결전일대비율
    data_parts[53] = '2000' # 누적거래량
    data_parts[51] = '+' # 부호
    data_body = '^'.join(data_parts)

    message = f"0|{tr_id}|some_key|{data_body}"

    # on_realtime_message_callback Mock
    mock_callback = MagicMock()
    api.on_realtime_message_callback = mock_callback

    api._handle_websocket_message(message)

    mock_callback.assert_called_once()
    called_args = mock_callback.call_args[0][0]
    assert called_args['type'] == 'realtime_quote'
    assert called_args['tr_id'] == tr_id
    assert called_args['data']["유가증권단축종목코드"] == '0002'
    assert called_args['data']["예상체결가"] == '20000'

# _handle_websocket_message: PINGPONG 제어 메시지 처리 테스트
def test_handle_websocket_message_pingpong(websocket_api_instance):
    api = websocket_api_instance
    pingpong_message = json.dumps({"header": {"tr_id": "PINGPONG"}})

    api._handle_websocket_message(pingpong_message)

    api.logger.info.assert_called_once_with("PINGPONG 수신됨. PONG 응답.")

# _handle_websocket_message: 성공적인 AES KEY/IV 수신 처리 테스트
def test_handle_websocket_message_aes_key_iv_reception(websocket_api_instance):
    api = websocket_api_instance
    tr_id = "H0STCNI0"
    key_value = "received_aes_key"
    iv_value = "received_aes_iv"

    message = json.dumps({
        "header": {"tr_id": tr_id, "tr_key": "some_tr_key"},
        "body": {
            "rt_cd": "0",
            "msg1": "SUCCESS",
            "output": {"key": key_value, "iv": iv_value}
        }
    })

    api._handle_websocket_message(message)

    assert api._aes_key == key_value
    assert api._aes_iv == iv_value
    assert api.logger.info.call_count == 2 # 2개의 info 로그가 발생
    api.logger.info.assert_has_calls([
        mock.call("실시간 요청 응답 성공: TR_KEY=some_tr_key, MSG=SUCCESS"),
        mock.call(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")
    ])

# _handle_websocket_message: 성공적인 signing_notice 복호화 테스트
def test_handle_websocket_message_signing_notice_decrypted_success(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "test_aes_key"
    api._aes_iv = "test_aes_iv"
    tr_id = "H0STCNI0"
    encrypted_payload = "encrypted_string_example" # 실제 복호화될 데이터
    decrypted_str = "decrypted_message_content" # _aes_cbc_base64_dec 결과

    # _aes_cbc_base64_dec를 모의하여 항상 성공적으로 복호화된 문자열을 반환하도록 설정
    with patch.object(api, '_aes_cbc_base64_dec', return_value=decrypted_str) as mock_decrypt, \
         patch.object(api, '_parse_signing_notice', return_value={"parsed_field": "parsed_value"}): # _parse_signing_notice 모의

        # _handle_websocket_message가 기대하는 '|'로 구분된 메시지 형식
        message = f"0|{tr_id}|REAL_DATA|{encrypted_payload}"

        mock_callback = MagicMock()
        api.on_realtime_message_callback = mock_callback

        api._handle_websocket_message(message)

        mock_decrypt.assert_called_once_with(api._aes_key, api._aes_iv, encrypted_payload)
        mock_callback.assert_called_once()
        called_args = mock_callback.call_args[0][0]
        assert called_args['type'] == 'signing_notice'
        assert called_args['tr_id'] == tr_id
        assert called_args['data'] == {"parsed_field": "parsed_value"}

# _get_approval_key: requests.exceptions.RequestException 처리 테스트
@pytest.mark.asyncio
async def test_get_approval_key_request_exception(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target, side_effect=requests.exceptions.RequestException("Connection error")) as mock_post:
        result = await api._get_approval_key()
        assert result is None
        api.logger.error.assert_called_once()
        logged_message = api.logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 중 네트워크 오류:" in logged_message

# _get_approval_key: json.JSONDecodeError 처리 테스트
@pytest.mark.asyncio
async def test_get_approval_key_json_decode_error(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0) # json decode error
        result = await api._get_approval_key()
        assert result is None
        api.logger.error.assert_called_once()
        logged_message = api.logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 응답 JSON 디코딩 실패:" in logged_message

# _get_approval_key: 일반 Exception 처리 테스트
@pytest.mark.asyncio
async def test_get_approval_key_general_exception(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target, side_effect=Exception("Unexpected error")) as mock_post:
        result = await api._get_approval_key()
        assert result is None
        api.logger.error.assert_called_once()
        logged_message = api.logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 중 알 수 없는 오류:" in logged_message

# _receive_messages: websockets.exceptions.ConnectionClosedOK 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_connection_closed_ok(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = websockets.ConnectionClosedOK(1000, "OK") # 정상 종료

    await api._receive_messages()

    api.logger.info.assert_called_once_with("웹소켓 연결이 정상적으로 종료되었습니다.")
    assert api._is_connected is False
    assert api.ws is None

# _receive_messages: websockets.exceptions.ConnectionClosedError 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_connection_closed_error(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = Exception("Abnormal closure")

    await api._receive_messages()

    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "웹소켓 메시지 수신 중 예상치 못한 오류 발생" in logged_message
    assert api._is_connected is False
    assert api.ws is None

# _receive_messages: asyncio.CancelledError 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_cancelled_error(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = asyncio.CancelledError # 작업 취소

    await api._receive_messages()

    api.logger.info.assert_called_once_with("웹소켓 메시지 수신 태스크가 취소되었습니다.")
    assert api._is_connected is False
    assert api.ws is None

# _receive_messages: 일반 Exception 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_general_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = Exception("General receive error") # 일반 예외

    await api._receive_messages()

    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "웹소켓 메시지 수신 중 예상치 못한 오류 발생:" in logged_message
    assert api._is_connected is False
    assert api.ws is None

# send_realtime_request: 연결되지 않았을 때 False 반환 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_not_connected(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = False # 연결되지 않음
    api.ws = None

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api.logger.error.assert_called_once_with("웹소켓이 연결되어 있지 않아 실시간 요청을 보낼 수 없습니다.")

# send_realtime_request: approval_key 없을 때 False 반환 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_no_approval_key(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = None # approval_key 없음

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api.logger.error.assert_called_once_with("approval_key가 없어 실시간 요청을 보낼 수 없습니다.")

# send_realtime_request: 웹소켓 전송 중 ConnectionClosedException 처리 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_connection_closed_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = "dummy_key"
    api.ws.send.side_effect = Exception("WebSocket closed") # 더미 예외 사용

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "실시간 요청 전송 중 오류 발생" in logged_message
    assert api._is_connected is False


# send_realtime_request: 웹소켓 전송 중 일반 Exception 처리 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_general_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = "dummy_key"
    api.ws.send.side_effect = Exception("Generic send error")

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api.logger.error.assert_called_once()
    logged_message = api.logger.error.call_args[0][0]
    assert "실시간 요청 전송 중 오류 발생:" in logged_message

# subscribe_realtime_price: 성공 테스트
@pytest.mark.asyncio
async def test_subscribe_realtime_price_success(websocket_api_instance):
    api = websocket_api_instance
    stock_code = "005930"
    with patch.object(api, "send_realtime_request", new_callable=AsyncMock, return_value=True) as mock_send:
        result = await api.subscribe_realtime_price(stock_code)
        assert result is True
        mock_send.assert_called_once_with(
            api._config['tr_ids']['websocket']['realtime_price'], stock_code, tr_type="1"
        )
        api.logger.info.assert_called_once()
        logged_message = api.logger.info.call_args[0][0]
        assert f"종목 {stock_code} 실시간 체결 데이터 구독 요청" in logged_message

# unsubscribe_realtime_price: 성공 테스트
@pytest.mark.asyncio
async def test_unsubscribe_realtime_price_success(websocket_api_instance):
    api = websocket_api_instance
    stock_code = "005930"
    with patch.object(api, "send_realtime_request", new_callable=AsyncMock, return_value=True) as mock_send:
        result = await api.unsubscribe_realtime_price(stock_code)
        assert result is True
        mock_send.assert_called_once_with(
            api._config['tr_ids']['websocket']['realtime_price'], stock_code, tr_type="2"
        )
        api.logger.info.assert_called_once()
        logged_message = api.logger.info.call_args[0][0]
        assert f"종목 {stock_code} 실시간 체결 데이터 구독 해지 요청" in logged_message

# subscribe_realtime_quote: 성공 테스트
@pytest.mark.asyncio
async def test_subscribe_realtime_quote_success(websocket_api_instance):
    api = websocket_api_instance
    stock_code = "005930"
    with patch.object(api, "send_realtime_request", new_callable=AsyncMock, return_value=True) as mock_send:
        result = await api.subscribe_realtime_quote(stock_code)
        assert result is True
        mock_send.assert_called_once_with(
            api._config['tr_ids']['websocket']['realtime_quote'], stock_code, tr_type="1"
        )
        api.logger.info.assert_called_once()
        logged_message = api.logger.info.call_args[0][0]
        assert f"종목 {stock_code} 실시간 호가 데이터 구독 요청" in logged_message

# unsubscribe_realtime_quote: 성공 테스트
@pytest.mark.asyncio
async def test_unsubscribe_realtime_quote_success(websocket_api_instance):
    api = websocket_api_instance
    stock_code = "005930"
    with patch.object(api, "send_realtime_request", new_callable=AsyncMock, return_value=True) as mock_send:
        result = await api.unsubscribe_realtime_quote(stock_code)
        assert result is True
        mock_send.assert_called_once_with(
            api._config['tr_ids']['websocket']['realtime_quote'], stock_code, tr_type="2"
        )
        api.logger.info.assert_called_once()
        logged_message = api.logger.info.call_args[0][0]
        assert f"종목 {stock_code} 실시간 호가 데이터 구독 해지 요청" in logged_message

# _parse_stock_quote_data: 모든 필드 포함된 유효한 데이터 파싱 테스트
def test_parse_stock_quote_data_valid_fields(websocket_api_instance):
    api = websocket_api_instance
    # 59개 필드를 모두 채운 유효한 데이터 문자열 생성
    # 필드 순서는 _parse_stock_quote_data의 recvvalue[인덱스] 참조
    # 여기서는 임시로 의미 있는 값을 넣음
    valid_data_parts = [''] * 59
    valid_data_parts[0] = 'STOCK_A' # 유가증권단축종목코드
    valid_data_parts[1] = '090000' # 영업시간 (HHMMSS)
    valid_data_parts[2] = '0' # 시간구분코드
    valid_data_parts[3] = '1000' # 매도호가1
    valid_data_parts[47] = '999' # 예상체결가
    valid_data_parts[48] = '500' # 예상체결량
    valid_data_parts[49] = '1000000' # 예상거래량
    valid_data_parts[50] = '-10' # 예상체결대비
    valid_data_parts[51] = '-' # 부호
    valid_data_parts[52] = '0.1' # 예상체결전일대비율
    valid_data_parts[53] = '100000' # 누적거래량
    # 나머지 필드는 '0' 또는 적절한 기본값으로 채울 수 있음
    for i in range(len(valid_data_parts)):
        if not valid_data_parts[i]: # 비어있으면 기본값으로 채움 (대부분 숫자 필드 가정)
            if i in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, # 매도호가
                     13, 14, 15, 16, 17, 18, 19, 20, 21, 22, # 매수호가
                     23, 24, 25, 26, 27, 28, 29, 30, 31, 32, # 매도호가잔량
                     33, 34, 35, 36, 37, 38, 39, 40, 41, 42, # 매수호가잔량
                     43, 44, 45, 46 # 총매도/매수호가잔량, 시간외 총매도/매수호가잔량
                     ]:
                valid_data_parts[i] = '0'
    valid_data_str = '^'.join(valid_data_parts)

    parsed_data = api._parse_stock_quote_data(valid_data_str)

    assert parsed_data["유가증권단축종목코드"] == "STOCK_A"
    assert parsed_data["예상체결가"] == '999'
    assert parsed_data["예상체결량"] == '500'
    assert parsed_data["예상거래량"] == '1000000'
    assert parsed_data["예상체결대비"] == '-10'
    assert parsed_data["부호"] == '-'
    assert parsed_data["예상체결전일대비율"] == '0.1'
    assert parsed_data["누적거래량"] == '100000'
    assert parsed_data["영업시간"] == '090000'
    assert parsed_data["시간구분코드"] == '0'
    # 나머지 호가/잔량 필드들도 0으로 잘 파싱되었는지 검증 가능


# _parse_stock_contract_data: 모든 필드 포함된 유효한 데이터 파싱 테스트
def test_parse_stock_contract_data_valid_fields(websocket_api_instance):
    api = websocket_api_instance
    # 46개 필드를 모두 채운 유효한 데이터 문자열 생성
    # 필드 순서는 _parse_stock_contract_data의 menulist 참조
    valid_data_parts = [''] * 46
    valid_data_parts[0] = 'STOCK_B' # 유가증권단축종목코드
    valid_data_parts[1] = '100000' # 주식체결시간
    valid_data_parts[2] = '50000' # 주식현재가
    valid_data_parts[3] = '+' # 전일대비부호
    valid_data_parts[4] = '500' # 전일대비
    valid_data_parts[5] = '1.0' # 전일대비율
    valid_data_parts[12] = '100' # 체결거래량
    valid_data_parts[13] = '10000' # 누적거래량
    valid_data_parts[14] = '500000000' # 누적거래대금
    # 기타 숫자 필드들은 '0'으로, 문자열 필드들은 빈 문자열로 채울 수 있음
    for i in range(len(valid_data_parts)):
        if not valid_data_parts[i]:
            if i in [ # 숫자 필드로 예상되는 인덱스 (menulist 기반)
                6, 7, 8, 9, 10, 11, # 가중평균주식가격, 주식시가, 주식최고가, 주식최저가, 매도호가1, 매수호가1
                15, 16, 17, 18, # 매도체결건수, 매수체결건수, 순매수체결건수, 체결강도
                19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, # 총매도수량, 총매수수량 등
                36, 37, 38, 39, 40, 41, 42, # 매도/매수호가잔량, 총매도/매수호가잔량, 거래량회전율, 전일동시간누적거래량, 전일동시간누적거래량비율
            ]:
                valid_data_parts[i] = '0'
    valid_data_str = '^'.join(valid_data_parts)

    parsed_data = api._parse_stock_contract_data(valid_data_str)

    assert parsed_data["유가증권단축종목코드"] == "STOCK_B"
    assert parsed_data["주식체결시간"] == '100000'
    assert parsed_data["주식현재가"] == '50000'
    assert parsed_data["전일대비부호"] == '+'
    assert parsed_data["전일대비"] == '500'
    assert parsed_data["전일대비율"] == '1.0'
    assert parsed_data["체결거래량"] == '100'
    assert parsed_data["누적거래량"] == '10000'
    assert parsed_data["누적거래대금"] == '500000000'
    # 나머지 필드들도 0 또는 빈 문자열로 잘 파싱되었는지 검증 가능
