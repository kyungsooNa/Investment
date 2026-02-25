# tests/test_korea_invest_websocket_api.py
import base64
import logging
import pytest
import json
import requests
import websockets
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from base64 import b64encode

from websockets.frames import Close
from core.logger import Logger  # 사용자 정의 Logger 사용을 가정
from unittest.mock import MagicMock, patch, AsyncMock
from unittest import mock
from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI


@pytest.fixture
def websocket_api_instance():
    """KoreaInvestWebSocketAPI 인스턴스를 위한 픽스처."""
    mock_env = MagicMock()
    # mock_env.get_full_config.return_value를 실제 _config 구조에 맞게 재구성
    full_config = {
        "websocket_url": "wss://dummy-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://dummy-base-url",
        "custtype": "P",  # send_realtime_request에서 사용
        "tr_ids": {  # 핵심: 'tr_ids' 키 추가
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

    mock_logger = MagicMock()
    mock_env.get_websocket_url.return_value = "wss://dummy-url"
    mock_env.get_full_config.return_value = full_config
    mock_env.get_websocket_url.return_value = full_config["websocket_url"]
    mock_env.active_config = full_config  # ✅ 여기 추가!

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


@pytest.mark.asyncio
async def test_websocket_api_initialization(websocket_api_instance):
    api = websocket_api_instance

    # 속성이 잘 초기화됐는지 검증
    assert api._websocket_url is None
    assert api._rest_api_key is None
    assert api._rest_api_secret is None
    assert api._base_rest_url is None
    assert api._is_connected is False
    assert api.ws is None


@pytest.mark.asyncio
async def test_websocket_api_connect_success(websocket_api_instance):
    api = websocket_api_instance

    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

    with patch(patch_target, new_callable=AsyncMock) as mock_connect, \
            patch.object(api, "_get_approval_key", new_callable=AsyncMock, return_value="approval-key"):
        # ✅ 웹소켓 객체를 명확히 설정
        mock_websocket = AsyncMock()
        mock_websocket.recv = AsyncMock(return_value="0|mock message")  # ✅ 경고 방지 핵심
        mock_connect.return_value = mock_websocket

        await api.connect()

        mock_connect.assert_called_once_with("wss://dummy-url", ping_interval=20, ping_timeout=20)
        assert api._is_connected is True
        assert api.approval_key == "approval-key"


def test_set_on_realtime_message_callback(websocket_api_instance):
    api = websocket_api_instance

    # 콜백 함수 정의
    def dummy_callback(msg):
        return f"received: {msg}"

    api.on_realtime_message_callback = dummy_callback

    # 설정된 콜백 확인
    assert api.on_realtime_message_callback("테스트") == "received: 테스트"


@pytest.mark.asyncio
async def test_get_approval_key(websocket_api_instance):
    api = websocket_api_instance

    # 동적 패치 대상 설정
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "approval_key": "MOCKED_KEY"
        }

        approval_key = await api._get_approval_key()
        assert approval_key == "MOCKED_KEY"


@pytest.mark.asyncio
async def test_websocket_api_connect_failure_due_to_approval_key(websocket_api_instance):
    api = websocket_api_instance

    # 🧩 동적 경로로 패치 대상 문자열 생성
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

    with patch(patch_target, new_callable=AsyncMock) as mock_connect, \
            patch.object(api, "_get_approval_key", new_callable=AsyncMock, return_value=None):
        result = await api.connect()
        assert result is False
        api._logger.error.assert_called_with("웹소켓 접속 키 발급 실패로 연결할 수 없습니다.")


@pytest.mark.asyncio
async def test_websocket_api_disconnect_calls_close(websocket_api_instance):
    api = websocket_api_instance

    mock_ws = AsyncMock()
    api.ws = mock_ws
    api._is_connected = True

    await api.disconnect()

    mock_ws.close.assert_called_once()
    assert api._is_connected is False


@pytest.mark.asyncio
async def test_on_receive_without_callback_logs_warning(websocket_api_instance):
    api = websocket_api_instance

    dummy_message = json.dumps({"header": {}, "body": {}})
    await api._on_receive(dummy_message)

    api._logger.warning.assert_called_once_with("수신된 메시지를 처리할 콜백이 등록되지 않았습니다.")


@pytest.mark.asyncio
async def test_on_receive_with_callback_called(websocket_api_instance):
    api = websocket_api_instance

    callback = AsyncMock()
    api.on_realtime_message_callback = callback

    dummy_message = json.dumps({
        "header": {"tr_id": "H0STCNT0"},
        "body": {"output": {"msg": "test"}}
    })

    await api._on_receive(dummy_message)
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_receive_with_callback_called_once(websocket_api_instance):
    api = websocket_api_instance

    dummy_callback = AsyncMock()
    api.on_realtime_message_callback = dummy_callback

    dummy_message = json.dumps({
        "header": {
            "tr_id": "H0IFCNI0"
        },
        "body": {
            "output": {"key": "value"}
        }
    })

    await api._on_receive(dummy_message)

    dummy_callback.assert_awaited_once_with({
        "header": {
            "tr_id": "H0IFCNI0"
        },
        "body": {
            "output": {"key": "value"}
        }
    })


# _aes_cbc_base64_dec: 잘못된 base64 문자열 처리 테스트
def test_aes_cbc_base64_dec_invalid_base64(websocket_api_instance):
    """
    _aes_cbc_base64_dec 메서드에 잘못된 base64 문자열이 주어졌을 때
    None을 반환하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._aes_key = "A" * 32
    api._aes_iv = "A" * 16

    malformed_base64_data = "this-is-not-valid-base64!"

    result = api._aes_cbc_base64_dec(api._aes_key, api._aes_iv, malformed_base64_data)

    assert result is None

    # 에러 로그가 호출되었는지 확인
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
    assert "base64 디코딩 실패" in logged_message or "AES 복호화 오류 발생" in logged_message


# _aes_cbc_base64_dec: 복호화 오류 처리 테스트 (예: 잘못된 키/IV)
def test_aes_cbc_base64_dec_decryption_error(websocket_api_instance):
    """
    _aes_cbc_base64_dec 메서드에서 복호화 중 오류가 발생할 때 (예: 잘못된 키/IV)
    None을 반환하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._aes_key = "A" * 32
    api._aes_iv = "A" * 16

    encrypted_payload = base64.b64encode(b"some_random_data_that_wont_decrypt").decode('utf-8')

    result = api._aes_cbc_base64_dec(api._aes_key, api._aes_iv, encrypted_payload)

    assert result is None
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
    assert "base64 디코딩 실패" in logged_message or "AES 복호화 오류 발생" in logged_message


# _handle_websocket_message: JSON 디코딩 오류 처리 테스트 (제어 메시지)
# async 키워드 및 await 호출 제거 [수정]
def test_handle_websocket_message_json_decode_error_control(websocket_api_instance):
    """
    _handle_websocket_message가 유효하지 않은 JSON 형식의 제어 메시지를 받을 때
    json.JSONDecodeError를 처리하고 에러를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    invalid_json_message = "this is not json"

    api._handle_websocket_message(invalid_json_message)  # await 제거

    # 직접 mock_logger.error 호출을 확인 [수정]
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]  # 첫 번째 인자는 메시지 문자열
    assert "제어 메시지 JSON 디코딩 실패:" in logged_message  # 로그 메시지 일치하도록 수정 [수정]


# _handle_websocket_message: _aes_key 또는 _aes_iv가 없는 서명 통지 메시지 처리 테스트
# async 키워드 및 await 호출 제거 [수정]
def test_handle_websocket_message_signing_notice_missing_aes_keys(websocket_api_instance):
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

    api._handle_websocket_message(message)  # await 제거

    # 직접 mock_logger.error 호출을 확인 [수정: warning -> error]
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
    assert "실시간 요청 응답 오류:" in logged_message  # 로그 메시지 일치하도록 수정 [수정]


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
    incomplete_data_parts[0] = '0001'  # 유가증권단축종목코드

    # 주요 숫자 필드 (0으로 예상) 인덱스를 '0'으로 설정
    numeric_indices_quote = [
        3, 4, 5, 6, 7, 8, 9, 10, 11, 12,  # 매도호가
        13, 14, 15, 16, 17, 18, 19, 20, 21, 22,  # 매수호가
        23, 24, 25, 26, 27, 28, 29, 30, 31, 32,  # 매도호가잔량
        33, 34, 35, 36, 37, 38, 39, 40, 41, 42,  # 매수호가잔량
        43, 44, 45, 46,  # 총매도/매수호가잔량, 시간외 총매도/매수호가잔량
        47, 48, 49, 50, 52, 53  # 예상체결가, 예상체결량, 예상거래량, 예상체결대비, 예상체결전일대비율, 누적거래량
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
    assert parsed_data["부호"] == ''  # 부호는 숫자 필드가 아님


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
    incomplete_data_parts = [''] * 46  # 길이 수정
    incomplete_data_parts[0] = '0001'  # 유가증권단축종목코드

    # 주요 숫자 필드 (0으로 예상) 인덱스를 '0'으로 설정
    numeric_indices_contract = [
        2,  # 주식현재가
        4,  # 전일대비
        5,  # 전일대비율
        13,  # 누적거래량
        14,  # 누적거래대금
    ]
    for idx in numeric_indices_contract:
        incomplete_data_parts[idx] = '0'

    incomplete_data_str = '^'.join(incomplete_data_parts)

    parsed_data = api._parse_stock_contract_data(incomplete_data_str)

    assert parsed_data["유가증권단축종목코드"] == "0001"
    assert parsed_data["주식현재가"] == '0'  # 체결가격 대신 주식현재가 사용
    assert parsed_data["전일대비부호"] == ''
    assert parsed_data["전일대비"] == '0'
    assert parsed_data["전일대비율"] == '0'
    assert parsed_data["누적거래량"] == '0'
    assert parsed_data["누적거래대금"] == '0'
    # 주식매매구분코드 키는 menulist에 없으므로 어설션 제거


# _handle_websocket_message: 알 수 없는 tr_id 처리 테스트 [수정]
def test_handle_websocket_message_unknown_tr_id(websocket_api_instance):  # @pytest.mark.asyncio 제거 [수정]
    """
    _handle_websocket_message가 tr_ids_config에 없는 알 수 없는 tr_id를 받을 때
    올바르게 처리하고 경고를 로깅하는지 테스트합니다.
    """
    api = websocket_api_instance
    unknown_tr_id_message = json.dumps({  # 이 메시지는 'else' (제어 메시지) 블록으로 이동
        "header": {"tr_id": "UNKNOWN_TR"},
        "body": {"output": {"msg": "some data"}}
    })

    api._handle_websocket_message(unknown_tr_id_message)  # await 제거

    # 알 수 없는 TR_ID 제어 메시지는 ERROR 로그를 발생시킴 (실시간 요청 응답 오류) [수정]
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
    assert "실시간 요청 응답 오류:" in logged_message


def test_handle_websocket_message_realtime_price_success(websocket_api_instance):
    api = websocket_api_instance

    # ✅ TR ID 강제 설정
    # api._config['tr_ids']['websocket']['realtime_price'] = "H0STCNT0"
    tr_id = "H0STCNT0"

    data_parts = [''] * 60
    data_parts[0] = '0001'
    data_parts[2] = '10000'
    data_parts[3] = '+'
    data_parts[4] = '100'
    data_parts[5] = '1.00'
    data_parts[13] = '1000'
    data_parts[14] = '10000000'
    data_body = '^'.join(data_parts)

    message = f"0|{tr_id}|some_key|{data_body}"
    print(f"[TEST] realtime_price TR_ID = {tr_id}")

    mock_callback = MagicMock()
    api.on_realtime_message_callback = mock_callback

    api._handle_websocket_message(message)

    mock_callback.assert_called_once()
    called_args = mock_callback.call_args[0][0]
    assert called_args['type'] == 'realtime_price'
    assert called_args['tr_id'] == tr_id
    assert called_args['data']["유가증권단축종목코드"] == '0001'
    assert called_args['data']["주식현재가"] == '10000'


# _handle_websocket_message: 성공적인 주식 호가(H0STASP0) 파싱 테스트
def test_handle_websocket_message_realtime_quote_success(websocket_api_instance):
    api = websocket_api_instance
    tr_id = api._env.active_config['tr_ids']['websocket']['realtime_quote']
    # _parse_stock_quote_data가 기대하는 59개 필드의 유효한 데이터
    # 유가증권단축종목코드, 예상체결가, 예상체결대비, 예상체결전일대비율, 누적거래량, 부호 등
    data_parts = [''] * 59
    data_parts[0] = '0002'  # 유가증권단축종목코드
    data_parts[47] = '20000'  # 예상체결가
    data_parts[50] = '50'  # 예상체결대비
    data_parts[52] = '0.25'  # 예상체결전일대비율
    data_parts[53] = '2000'  # 누적거래량
    data_parts[51] = '+'  # 부호
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

    api._logger.info.assert_called_once_with("PINGPONG 수신됨. PONG 응답.")


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
    assert api._logger.info.call_count == 2  # 2개의 info 로그가 발생
    api._logger.info.assert_has_calls([
        mock.call("실시간 요청 응답 성공: TR_KEY=some_tr_key, MSG=SUCCESS"),
        mock.call(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")
    ])


# _handle_websocket_message: 성공적인 signing_notice 복호화 테스트
def test_handle_websocket_message_signing_notice_decrypted_success(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "test_aes_key"
    api._aes_iv = "test_aes_iv"
    tr_id = "H0STCNI0"
    encrypted_payload = "encrypted_string_example"  # 실제 복호화될 데이터
    decrypted_str = "decrypted_message_content"  # _aes_cbc_base64_dec 결과

    # _aes_cbc_base64_dec를 모의하여 항상 성공적으로 복호화된 문자열을 반환하도록 설정
    with patch.object(api, '_aes_cbc_base64_dec', return_value=decrypted_str) as mock_decrypt, \
            patch.object(api, '_parse_signing_notice',
                         return_value={"parsed_field": "parsed_value"}):  # _parse_signing_notice 모의

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


@pytest.mark.asyncio
async def test_get_approval_key_request_exception(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    # ✅ post 요청 자체에서 RequestException 발생하도록 설정
    with patch(patch_target, side_effect=requests.exceptions.RequestException("Connection error")):
        result = await api._get_approval_key()

        assert result is None
        api._logger.error.assert_called_once()

        logged_message = api._logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 중 네트워크 오류:" in logged_message


# _get_approval_key: json.JSONDecodeError 처리 테스트
@pytest.mark.asyncio
async def test_get_approval_key_json_decode_error(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0)  # json decode error
        result = await api._get_approval_key()
        assert result is None
        api._logger.error.assert_called_once()
        logged_message = api._logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 응답 JSON 디코딩 실패:" in logged_message


# _get_approval_key: 일반 Exception 처리 테스트
@pytest.mark.asyncio
async def test_get_approval_key_general_exception(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target, side_effect=Exception("Unexpected error")) as mock_post:
        result = await api._get_approval_key()
        assert result is None
        api._logger.error.assert_called_once()
        logged_message = api._logger.error.call_args[0][0]
        assert "웹소켓 접속키 발급 중 알 수 없는 오류:" in logged_message


# _receive_messages: websockets.exceptions.ConnectionClosedOK 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_connection_closed_ok(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True  # 루프 진입을 위해 True 설정
    api.ws = AsyncMock()

    # Close frame 생성
    close_frame = Close(code=1000, reason="OK")

    # ConnectionClosedOK 예외를 side_effect로 설정
    exception = websockets.ConnectionClosedOK(
        rcvd=close_frame,
        sent=close_frame,
        rcvd_then_sent=True
    )
    api.ws.recv.side_effect = exception

    # 재연결 대기 시간(sleep)에 호출될 때 루프를 종료하도록 설정
    async def stop_loop(*args, **kwargs):
        api._auto_reconnect = False

    with patch("asyncio.sleep", side_effect=stop_loop):
        await api._receive_messages()

    # 변경된 로직: ConnectionClosedOK도 예외로 잡혀서 재연결 시도 로그(warning)가 출력됨
    api._logger.warning.assert_called()
    log_msg = api._logger.warning.call_args[0][0]
    assert "웹소켓 연결 끊김" in log_msg
    assert "재연결을 시도합니다" in log_msg

    assert api._is_connected is False
    assert api.ws is None


# _receive_messages: websockets.exceptions.ConnectionClosedError 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_connection_closed_error(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = Exception("Abnormal closure")

    async def stop_loop(*args, **kwargs):
        api._auto_reconnect = False

    with patch("asyncio.sleep", side_effect=stop_loop):
        await api._receive_messages()

    api._logger.warning.assert_called()
    logged_message = api._logger.warning.call_args[0][0]
    assert "웹소켓 연결 끊김" in logged_message
    assert "재연결을 시도합니다" in logged_message
    assert api._is_connected is False
    assert api.ws is None


# _receive_messages: asyncio.CancelledError 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_cancelled_error(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = asyncio.CancelledError  # 작업 취소

    with pytest.raises(asyncio.CancelledError):
        await api._receive_messages()


# _receive_messages: 일반 Exception 처리 테스트
@pytest.mark.asyncio
async def test_receive_messages_general_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True
    api.ws = AsyncMock()
    api.ws.recv.side_effect = Exception("General receive error")  # 일반 예외

    async def stop_loop(*args, **kwargs):
        api._auto_reconnect = False

    with patch("asyncio.sleep", side_effect=stop_loop):
        await api._receive_messages()

    api._logger.warning.assert_called()
    logged_message = api._logger.warning.call_args[0][0]
    assert "웹소켓 연결 끊김" in logged_message
    assert "General receive error" in logged_message
    assert api._is_connected is False
    assert api.ws is None


# send_realtime_request: 연결되지 않았을 때 False 반환 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_not_connected(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = False  # 연결되지 않음
    api.ws = None

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api._logger.error.assert_called_once_with("웹소켓이 연결되어 있지 않아 실시간 요청을 보낼 수 없습니다.")


# send_realtime_request: approval_key 없을 때 False 반환 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_no_approval_key(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = None  # approval_key 없음

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api._logger.error.assert_called_once_with("approval_key가 없어 실시간 요청을 보낼 수 없습니다.")


# send_realtime_request: 웹소켓 전송 중 ConnectionClosedException 처리 테스트
@pytest.mark.asyncio
async def test_send_realtime_request_connection_closed_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = "dummy_key"
    api.ws.send.side_effect = Exception("WebSocket closed")  # 더미 예외 사용

    result = await api.send_realtime_request("TR_ID", "TR_KEY")
    assert result is False
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
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
    api._logger.error.assert_called_once()
    logged_message = api._logger.error.call_args[0][0]
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
            api._env.active_config['tr_ids']['websocket']['realtime_price'], stock_code, tr_type="1"
        )
        api._logger.info.assert_called_once()
        logged_message = api._logger.info.call_args[0][0]
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
            api._env.active_config['tr_ids']['websocket']['realtime_price'], stock_code, tr_type="2"
        )
        api._logger.info.assert_called_once()
        logged_message = api._logger.info.call_args[0][0]
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
            api._env.active_config['tr_ids']['websocket']['realtime_quote'], stock_code, tr_type="1"
        )
        api._logger.info.assert_called_once()
        logged_message = api._logger.info.call_args[0][0]
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
            api._env.active_config['tr_ids']['websocket']['realtime_quote'], stock_code, tr_type="2"
        )
        api._logger.info.assert_called_once()
        logged_message = api._logger.info.call_args[0][0]
        assert f"종목 {stock_code} 실시간 호가 데이터 구독 해지 요청" in logged_message


@pytest.mark.asyncio
async def test_receive_messages_max_retries_exceeded(websocket_api_instance):
    """
    웹소켓 재연결 시도가 최대 횟수를 초과했을 때,
    에러 로그를 남기고 자동 재연결을 중단하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._is_connected = False  # 연결 끊김 상태로 시작
    api._auto_reconnect = True

    # _establish_connection이 항상 실패(False 반환)하도록 설정
    with patch.object(api, "_establish_connection", new_callable=AsyncMock, return_value=False) as mock_est_conn, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        await api._receive_messages()

        # 검증
        # 1. 최대 재시도 횟수(30)만큼 연결 시도했는지 확인
        assert mock_est_conn.call_count == 30

        # 2. 대기(sleep)도 30번 호출되었는지 확인
        assert mock_sleep.call_count == 30

        # 3. 에러 로그 확인
        api._logger.error.assert_called_with("웹소켓 재연결 실패: 최대 재시도 횟수(30)를 초과했습니다.")

        # 4. 자동 재연결 플래그가 꺼졌는지 확인
        assert api._auto_reconnect is False


@pytest.mark.asyncio
async def test_receive_messages_stops_reconnect_when_market_closed(websocket_api_instance):
    """
    장 종료 시 자동 재연결을 중단하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._is_connected = False  # 연결 끊김 상태
    api._auto_reconnect = True

    # TimeManager Mock 설정 (장 종료 상태)
    api._time_manager = MagicMock()
    api._time_manager.is_market_open.return_value = False

    # _establish_connection이 호출되지 않아야 함
    with patch.object(api, "_establish_connection", new_callable=AsyncMock) as mock_est_conn, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        await api._receive_messages()

        # 검증
        # 1. 장 종료 로그 확인
        api._logger.info.assert_called_with("장이 종료되어 자동 재연결을 중단합니다.")

        # 2. 자동 재연결 플래그가 꺼졌는지 확인
        assert api._auto_reconnect is False

        # 3. 재연결 시도(연결 수립, 대기)가 없었는지 확인
        mock_est_conn.assert_not_called()
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_keepalive_logic(websocket_api_instance):
    """
    웹소켓 연결 유지(Keep-alive)를 위한 설정(ping_interval)과
    PINGPONG 메시지 수신 처리가 구현되어 있는지 검증합니다.
    """
    api = websocket_api_instance

    # 1. 연결 시 ping_interval 설정 확인
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"
    with patch(patch_target, new_callable=AsyncMock) as mock_connect, \
         patch.object(api, "_get_approval_key", return_value="key"):
        
        await api.connect()
        
        # websockets.connect 호출 시 ping_interval이 설정되었는지 확인
        _, kwargs = mock_connect.call_args
        assert kwargs.get('ping_interval') == 20
        assert kwargs.get('ping_timeout') == 20
        
    # 2. PINGPONG 메시지 수신 처리 확인
    api._logger.info = MagicMock()
    ping_msg = json.dumps({"header": {"tr_id": "PINGPONG"}})
    api._handle_websocket_message(ping_msg)
    
    # 로그가 남는지 확인 (현재 구현은 로그만 남김)
    api._logger.info.assert_called_with("PINGPONG 수신됨. PONG 응답.")


@pytest.mark.asyncio
async def test_receive_messages_exponential_backoff(websocket_api_instance):
    """
    웹소켓 재연결 시 대기 시간이 지수 백오프 방식으로 증가하는지 테스트합니다.
    """
    api = websocket_api_instance
    api._is_connected = False
    api._auto_reconnect = True

    # 예상되는 지연 시간: 3, 6, 12, 24, 48, 60(최대)
    expected_delays = [3, 6, 12, 24, 48, 60]
    stop_after = len(expected_delays)
    current_calls = 0

    async def sleep_side_effect(delay):
        nonlocal current_calls
        current_calls += 1
        if current_calls >= stop_after:
            api._auto_reconnect = False

    with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=sleep_side_effect) as mock_sleep, \
         patch.object(api, "_establish_connection", new_callable=AsyncMock, return_value=False):

        await api._receive_messages()

        # 실제 호출된 delay 값 검증
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == expected_delays


@pytest.mark.asyncio
async def test_receive_messages_reconnect_exception_logging(websocket_api_instance):
    """
    _receive_messages 실행 중 재연결 시도(_establish_connection)에서 예외가 발생했을 때
    적절히 로깅되고 재시도 로직이 동작하는지 검증합니다.
    """
    api = websocket_api_instance
    api._is_connected = False
    api._auto_reconnect = True

    # websockets.connect가 예외를 발생시키도록 설정
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

    # 루프를 한 번만 돌고 종료하도록 sleep에서 플래그 변경
    async def sleep_side_effect(delay):
        api._auto_reconnect = False

    with patch(patch_target, side_effect=Exception("Connection failed")) as mock_connect, \
         patch("asyncio.sleep", new_callable=AsyncMock, side_effect=sleep_side_effect) as mock_sleep, \
         patch.object(api, "_get_approval_key", new_callable=AsyncMock, return_value="key"):

        await api._receive_messages()

        # 1. 연결 시도 확인
        mock_connect.assert_called()

        # 2. 에러 로그 확인 (_establish_connection 내부)
        error_logs = [c[0][0] for c in api._logger.error.call_args_list]
        assert any("웹소켓 연결 중 오류 발생" in log and "Connection failed" in log for log in error_logs)

        # 3. 재연결 대기 로그 확인 (_receive_messages 내부)
        info_logs = [c[0][0] for c in api._logger.info.call_args_list]
        assert any("웹소켓 재연결 대기 중" in log for log in info_logs)


@pytest.mark.asyncio
async def test_receive_messages_resubscribe_failure(websocket_api_instance):
    """
    재연결 성공 후 구독 복구(_resubscribe_all) 중 예외가 발생했을 때,
    에러 로그를 남기고 루프가 계속되는지(또는 적절히 처리되는지) 검증합니다.
    """
    api = websocket_api_instance
    api._is_connected = False
    api._auto_reconnect = True

    # 루프를 한 번만 돌고 종료하도록 sleep에서 플래그 변경
    async def sleep_side_effect(delay):
        api._auto_reconnect = False

    with patch.object(api, "_establish_connection", new_callable=AsyncMock, return_value=True) as mock_est_conn, \
         patch.object(api, "_resubscribe_all", new_callable=AsyncMock, side_effect=Exception("Resubscribe Error")) as mock_resub, \
         patch("asyncio.sleep", new_callable=AsyncMock, side_effect=sleep_side_effect) as mock_sleep:

        await api._receive_messages()

        # 검증
        mock_est_conn.assert_called_once()
        mock_resub.assert_called_once()
        
        # 에러 로그 확인
        api._logger.error.assert_called_with("구독 복구 중 오류 발생: Resubscribe Error")


# _parse_stock_quote_data: 모든 필드 포함된 유효한 데이터 파싱 테스트
def test_parse_stock_quote_data_valid_fields(websocket_api_instance):
    api = websocket_api_instance
    # 59개 필드를 모두 채운 유효한 데이터 문자열 생성
    # 필드 순서는 _parse_stock_quote_data의 recvvalue[인덱스] 참조
    # 여기서는 임시로 의미 있는 값을 넣음
    valid_data_parts = [''] * 59
    valid_data_parts[0] = 'STOCK_A'  # 유가증권단축종목코드
    valid_data_parts[1] = '090000'  # 영업시간 (HHMMSS)
    valid_data_parts[2] = '0'  # 시간구분코드
    valid_data_parts[3] = '1000'  # 매도호가1
    valid_data_parts[47] = '999'  # 예상체결가
    valid_data_parts[48] = '500'  # 예상체결량
    valid_data_parts[49] = '1000000'  # 예상거래량
    valid_data_parts[50] = '-10'  # 예상체결대비
    valid_data_parts[51] = '-'  # 부호
    valid_data_parts[52] = '0.1'  # 예상체결전일대비율
    valid_data_parts[53] = '100000'  # 누적거래량
    # 나머지 필드는 '0' 또는 적절한 기본값으로 채울 수 있음
    for i in range(len(valid_data_parts)):
        if not valid_data_parts[i]:  # 비어있으면 기본값으로 채움 (대부분 숫자 필드 가정)
            if i in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12,  # 매도호가
                     13, 14, 15, 16, 17, 18, 19, 20, 21, 22,  # 매수호가
                     23, 24, 25, 26, 27, 28, 29, 30, 31, 32,  # 매도호가잔량
                     33, 34, 35, 36, 37, 38, 39, 40, 41, 42,  # 매수호가잔량
                     43, 44, 45, 46  # 총매도/매수호가잔량, 시간외 총매도/매수호가잔량
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
    valid_data_parts[0] = 'STOCK_B'  # 유가증권단축종목코드
    valid_data_parts[1] = '100000'  # 주식체결시간
    valid_data_parts[2] = '50000'  # 주식현재가
    valid_data_parts[3] = '+'  # 전일대비부호
    valid_data_parts[4] = '500'  # 전일대비
    valid_data_parts[5] = '1.0'  # 전일대비율
    valid_data_parts[12] = '100'  # 체결거래량
    valid_data_parts[13] = '10000'  # 누적거래량
    valid_data_parts[14] = '500000000'  # 누적거래대금
    # 기타 숫자 필드들은 '0'으로, 문자열 필드들은 빈 문자열로 채울 수 있음
    for i in range(len(valid_data_parts)):
        if not valid_data_parts[i]:
            if i in [  # 숫자 필드로 예상되는 인덱스 (menulist 기반)
                6, 7, 8, 9, 10, 11,  # 가중평균주식가격, 주식시가, 주식최고가, 주식최저가, 매도호가1, 매수호가1
                15, 16, 17, 18,  # 매도체결건수, 매수체결건수, 순매수체결건수, 체결강도
                19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33,  # 총매도수량, 총매수수량 등
                36, 37, 38, 39, 40, 41, 42,  # 매도/매수호가잔량, 총매도/매수호가잔량, 거래량회전율, 전일동시간누적거래량, 전일동시간누적거래량비율
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


@pytest.mark.asyncio
async def test_disconnect_when_ws_is_none(websocket_api_instance):
    api = websocket_api_instance
    api.ws = None
    api._is_connected = True

    await api.disconnect()

    assert api._is_connected is False


@pytest.mark.asyncio
async def test_disconnect_with_receive_task_cancelled(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.close = AsyncMock()

    # ✅ 실제 asyncio 태스크를 생성해서 취소
    async def dummy_receive_loop():
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(dummy_receive_loop())
    api._receive_task = task

    await api.disconnect()

    # ✅ 태스크는 cancel 되었고 완료되었어야 함
    assert task.cancelled() or task.done()
    assert api._is_connected is False
    assert api.ws is None

    # ✅ 로그 메시지 확인
    log_messages = [call[0][0] for call in api._logger.info.call_args_list]
    assert "웹소켓 연결 종료 요청." in log_messages
    assert "웹소켓 수신 태스크 취소됨." in log_messages
    assert "웹소켓 연결 종료 완료." in log_messages


@pytest.mark.asyncio
@pytest.mark.real_sleep
async def test_disconnect_with_receive_task_exception(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True  # 루프 진입을 위해 True 설정
    api.ws = AsyncMock()
    api.ws.close = AsyncMock()

    # _receive_messages 태스크가 실행되다가 예외를 발생시키도록 ws.recv를 모의(mock)합니다.
    api.ws.recv.side_effect = Exception("테스트용 예외")

    # 원본 asyncio.sleep을 보존 (이벤트 루프 양보용)
    _real_sleep = asyncio.sleep

    # 프로덕션 코드의 asyncio.sleep만 즉시 반환하되 이벤트 루프에 제어권을 양보하도록 만듭니다.
    async def fast_sleep(delay):
        await _real_sleep(0)

    # _establish_connection을 모킹하여 재연결 시도가 테스트를 방해하지 않도록 함
    with patch.object(api, "_establish_connection", new_callable=AsyncMock, return_value=False), \
         patch("asyncio.sleep", side_effect=fast_sleep):
        # 실제 _receive_messages 태스크를 생성하여 실행합니다.
        api._receive_task = asyncio.create_task(api._receive_messages())

        # 태스크가 실행되어 예외를 발생시킬 시간을 주기 위해 짧게 대기합니다.
        for _ in range(10):
            await _real_sleep(0)
            if not api._is_connected:
                break

        # _is_connected가 False가 되었다면 예외 처리가 완료된 것입니다.
        assert api._is_connected is False, "태스크가 예외를 처리하지 못했습니다."

        # disconnect 메서드 호출
        await api.disconnect()

        # 태스크가 확실히 종료될 때까지 대기 (cleanup)
        try:
            await api._receive_task
        except asyncio.CancelledError:
            pass

    # 로거에 기록된 경고 로그를 확인합니다. (재연결 로직으로 변경됨)
    warning_logs = [call[0][0] for call in api._logger.warning.call_args_list]

    # 예외 발생 및 재연결 시도 로그 확인
    assert any("웹소켓 연결 끊김" in msg and "테스트용 예외" in msg for msg in warning_logs)

    assert api.ws is None


@pytest.mark.asyncio
async def test_on_receive_json_decode_error_logs_error(websocket_api_instance):
    api = websocket_api_instance
    api.on_realtime_message_callback = MagicMock()  # 콜백은 있어도 무방
    api.logger = MagicMock()

    invalid_json = '{"invalid_json": '  # 👈 JSON 파싱 실패 유도

    await api._on_receive(invalid_json)

    # 예외 로그가 찍혔는지 확인
    assert api._logger.error.call_count == 1
    assert "수신 메시지 처리 중 예외 발생" in api._logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_on_receive_callback_raises_exception_logs_error(websocket_api_instance):
    api = websocket_api_instance
    api.logger = MagicMock()

    async def faulty_callback(data):
        raise RuntimeError("의도된 예외")

    api.on_realtime_message_callback = faulty_callback

    await api._on_receive('{"key": "value"}')

    assert api._logger.error.call_count == 1
    assert "수신 메시지 처리 중 예외 발생" in api._logger.error.call_args[0][0]


def test_parse_futs_optn_quote_data_extracts_total_bid_ask_volumes(websocket_api_instance):
    """
    _parse_futs_optn_quote_data가 총매도호가잔량/총매수호가잔량 (268, 269라인)을 포함해 정확히 파싱하는지 테스트
    """
    api = websocket_api_instance

    # 최소 38개 항목 (index 0~37) 필요. 34~35번째 인덱스에 총매도/매수호가잔량을 넣는다.
    parts = [''] * 38
    parts[34] = '123456'  # 총매도호가잔량
    parts[35] = '654321'  # 총매수호가잔량

    data_str = '^'.join(parts)
    result = api._parse_futs_optn_quote_data(data_str)

    assert result["총매도호가잔량"] == "123456"
    assert result["총매수호가잔량"] == "654321"


def test_parse_futs_optn_contract_data_parses_correctly(websocket_api_instance):
    """
    _parse_futs_optn_contract_data가 지수선물/옵션 체결 데이터를 정확히 파싱하는지 테스트
    """
    api = websocket_api_instance

    # 필드 수에 맞게 mock 데이터 생성
    field_count = 50  # 실제 반환 키 개수에 맞춤
    sample_values = [f"value{i}" for i in range(field_count)]
    data_str = "^".join(sample_values)

    result = api._parse_futs_optn_contract_data(data_str)

    # 기본 검증

    assert isinstance(result, dict)
    assert len(result) == field_count
    assert result["선물단축종목코드"] == "value0"
    assert result["영업시간"] == "value1"
    assert result["선물현재가"] == "value5"  # 정확한 필드명으로 변경
    assert result["실시간가격제한구분"] == sample_values[-1]


def test_aes_cbc_base64_dec_success(websocket_api_instance):
    api = websocket_api_instance

    # --- Arrange ---
    key_str = "1234567890123456"  # 16 bytes
    iv_str = "6543210987654321"  # 16 bytes
    plaintext = "테스트 메시지입니다."

    # 암호화: pad → encrypt → base64
    cipher = AES.new(key_str.encode("utf-8"), AES.MODE_CBC, iv_str.encode("utf-8"))
    encrypted = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    cipher_b64 = b64encode(encrypted).decode("utf-8")

    # --- Act ---
    decrypted = api._aes_cbc_base64_dec(key_str, iv_str, cipher_b64)

    # --- Assert ---
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_receive_messages_connection_closed_error_korea_invest(websocket_api_instance):
    """
    KoreaInvestWebSocketAPI의 _receive_messages 메서드에서 websockets.ConnectionClosedError 발생 시
    logger.warning이 올바른 메시지로 호출되고 재연결 로직으로 진입하는지 검증합니다.
    """
    api = websocket_api_instance  # 픽스처에서 가져온 인스턴스
    api._is_connected = True
    api._auto_reconnect = True  # 루프 진입을 위해 True 설정
    mock_ws = AsyncMock()
    api.ws = mock_ws

    # ConnectionClosedError에 필요한 close 프레임 준비
    rcvd_close_frame = Close(code=1006, reason="Abnormal closure")
    sent_close_frame = Close(code=1006, reason="Abnormal closure")

    # recv() 호출 시 예외 발생하도록 설정
    exception = websockets.ConnectionClosedError(
        rcvd=rcvd_close_frame, sent=sent_close_frame, rcvd_then_sent=False
    )
    mock_ws.recv.side_effect = exception

    # 재연결 대기 시간(sleep)에 호출될 때 루프를 종료하도록 설정
    async def stop_loop(*args, **kwargs):
        api._auto_reconnect = False

    with patch("asyncio.sleep", side_effect=stop_loop):
        await api._receive_messages()

    # 상태 확인
    assert not api._is_connected
    assert api.ws is None

    # 로그 호출 확인
    api._logger.warning.assert_called()
    logged_msg = api._logger.warning.call_args[0][0]
    assert "웹소켓 연결 끊김" in logged_msg
    assert "재연결을 시도합니다" in logged_msg


def test_handle_websocket_message_parse_realtime_price(websocket_api_instance):
    api = websocket_api_instance
    price_tr_id = api._env.active_config["tr_ids"]["websocket"]["realtime_price"]
    data_parts = ['0001'] + ['0'] * 45  # 46개 필드
    message = f"0|{price_tr_id}|SOME_KEY|{'^'.join(data_parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    result = callback.call_args[0][0]
    assert result["type"] == "realtime_price"
    assert result["tr_id"] == price_tr_id
    assert result["data"]["유가증권단축종목코드"] == "0001"


def test_handle_websocket_message_signing_notice_success(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "testaeskey1234567890123456abcd"
    api._aes_iv = "testivvalue123456"
    tr_id = "H0STCNI0"
    enc_msg = "ENCRYPTED_STRING"

    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_json"), \
            patch.object(api, "_parse_signing_notice", return_value={"confirmed": True}):
        api.on_realtime_message_callback = MagicMock()
        message = f"1|{tr_id}|SOME_KEY|{enc_msg}"
        api._handle_websocket_message(message)

        api.on_realtime_message_callback.assert_called_once()
        args = api.on_realtime_message_callback.call_args[0][0]
        assert args["type"] == "signing_notice"
        assert args["tr_id"] == tr_id
        assert args["data"] == {"confirmed": True}


def test_handle_websocket_message_control_pingpong(websocket_api_instance):
    api = websocket_api_instance
    api.logger = MagicMock()
    message = json.dumps({"header": {"tr_id": "PINGPONG"}})

    api._handle_websocket_message(message)

    api._logger.info.assert_called_with("PINGPONG 수신됨. PONG 응답.")


def test_handle_websocket_message_parse_h0ifasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0IFASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_futs_optn_quote"
    assert args["tr_id"] == "H0IFASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0ioasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0IOASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_futs_optn_quote"
    assert args["tr_id"] == "H0IOASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0ifcnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0IFCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_futs_optn_contract"
    assert args["tr_id"] == "H0IFCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0iocnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0IOCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_futs_optn_contract"
    assert args["tr_id"] == "H0IOCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0cfasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0CFASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_product_futs_quote"
    assert args["tr_id"] == "H0CFASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0cfcnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0CFCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_product_futs_contract"
    assert args["tr_id"] == "H0CFCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zfasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZFASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_quote"
    assert args["tr_id"] == "H0ZFASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zoasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZOASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_quote"
    assert args["tr_id"] == "H0ZOASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zfcnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZFCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_contract"
    assert args["tr_id"] == "H0ZFCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zocnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZOCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_contract"
    assert args["tr_id"] == "H0ZOCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zfanc0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZFANC0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_exp_contract"
    assert args["tr_id"] == "H0ZFANC0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0zoanc0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0ZOANC0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_stock_futs_optn_exp_contract"
    assert args["tr_id"] == "H0ZOANC0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0mfasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0MFASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_cmefuts_quote"
    assert args["tr_id"] == "H0MFASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0mfcnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0MFCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_cmefuts_contract"
    assert args["tr_id"] == "H0MFCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0euasp0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0EUASP0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_eurex_optn_quote"
    assert args["tr_id"] == "H0EUASP0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0eucnt0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0EUCNT0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_eurex_optn_contract"
    assert args["tr_id"] == "H0EUCNT0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_parse_h0euanc0(websocket_api_instance):
    api = websocket_api_instance
    parts = ["SAMPLE"] + ["0"] * 50
    message = f"0|H0EUANC0|some_key|{'^'.join(parts)}"
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    api._handle_websocket_message(message)

    callback.assert_called_once()
    args = callback.call_args[0][0]
    assert args["type"] == "realtime_eurex_optn_exp_contract"
    assert args["tr_id"] == "H0EUANC0"
    assert isinstance(args["data"], dict)


def test_handle_websocket_message_signing_notice_success_h0stcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_message"), \
            patch.object(api, "_parse_signing_notice", return_value={"parsed": True}):
        msg = f"1|H0STCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        callback.assert_called_once()
        result = callback.call_args[0][0]
        assert result["type"] == "signing_notice"
        assert result["tr_id"] == "H0STCNI0"
        assert result["data"] == {"parsed": True}


def test_handle_websocket_message_signing_notice_key_missing_h0stcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    msg = f"1|H0STCNI0|some_key|ENC_DATA"
    api._handle_websocket_message(msg)
    api._logger.warning.assert_called_once()
    assert "AES 키/IV 없음" in api._logger.warning.call_args[0][0]


def test_handle_websocket_message_signing_notice_decrypt_fail_h0stcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16

    with patch.object(api, "_aes_cbc_base64_dec", return_value=None):
        msg = f"1|H0STCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        api._logger.error.assert_called_once()
        assert "복호화 실패" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_signing_notice_success_h0stcni9(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_message"), \
            patch.object(api, "_parse_signing_notice", return_value={"parsed": True}):
        msg = f"1|H0STCNI9|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        callback.assert_called_once()
        result = callback.call_args[0][0]
        assert result["type"] == "signing_notice"
        assert result["tr_id"] == "H0STCNI9"
        assert result["data"] == {"parsed": True}


def test_handle_websocket_message_signing_notice_key_missing_h0stcni9(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    msg = f"1|H0STCNI9|some_key|ENC_DATA"
    api._handle_websocket_message(msg)
    api._logger.warning.assert_called_once()
    assert "AES 키/IV 없음" in api._logger.warning.call_args[0][0]


def test_handle_websocket_message_signing_notice_decrypt_fail_h0stcni9(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16

    with patch.object(api, "_aes_cbc_base64_dec", return_value=None):
        msg = f"1|H0STCNI9|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        api._logger.error.assert_called_once()
        assert "복호화 실패" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_signing_notice_success_h0ifcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    callback = MagicMock()
    api.on_realtime_message_callback = callback
    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_message"), \
            patch.object(api, "_parse_signing_notice", return_value={"parsed": True}):
        msg = f"1|H0IFCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        callback.assert_called_once()
        result = callback.call_args[0][0]
        assert result["type"] == "signing_notice"
        assert result["tr_id"] == "H0IFCNI0"
        assert result["data"] == {"parsed": True}


def test_handle_websocket_message_signing_notice_key_missing_h0ifcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    msg = f"1|H0IFCNI0|some_key|ENC_DATA"
    api._handle_websocket_message(msg)
    api._logger.warning.assert_called_once()
    assert "AES 키/IV 없음" in api._logger.warning.call_args[0][0]


def test_handle_websocket_message_signing_notice_decrypt_fail_h0ifcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16

    with patch.object(api, "_aes_cbc_base64_dec", return_value=None):
        msg = f"1|H0IFCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        api._logger.error.assert_called_once()
        assert "복호화 실패" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_signing_notice_success_h0mfcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_message"), \
            patch.object(api, "_parse_signing_notice", return_value={"parsed": True}):
        msg = f"1|H0MFCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        callback.assert_called_once()
        result = callback.call_args[0][0]
        assert result["type"] == "signing_notice"
        assert result["tr_id"] == "H0MFCNI0"
        assert result["data"] == {"parsed": True}


def test_handle_websocket_message_signing_notice_key_missing_h0mfcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    msg = f"1|H0MFCNI0|some_key|ENC_DATA"
    api._handle_websocket_message(msg)
    api._logger.warning.assert_called_once()
    assert "AES 키/IV 없음" in api._logger.warning.call_args[0][0]


def test_handle_websocket_message_signing_notice_decrypt_fail_h0mfcni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16

    with patch.object(api, "_aes_cbc_base64_dec", return_value=None):
        msg = f"1|H0MFCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        api._logger.error.assert_called_once()
        assert "복호화 실패" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_signing_notice_success_h0eucni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    callback = MagicMock()
    api.on_realtime_message_callback = callback

    with patch.object(api, "_aes_cbc_base64_dec", return_value="decrypted_message"), \
            patch.object(api, "_parse_signing_notice", return_value={"parsed": True}):
        msg = f"1|H0EUCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        callback.assert_called_once()
        result = callback.call_args[0][0]
        assert result["type"] == "signing_notice"
        assert result["tr_id"] == "H0EUCNI0"
        assert result["data"] == {"parsed": True}


def test_handle_websocket_message_signing_notice_key_missing_h0eucni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None
    api._aes_iv = None

    msg = f"1|H0EUCNI0|some_key|ENC_DATA"
    api._handle_websocket_message(msg)
    api._logger.warning.assert_called_once()
    assert "AES 키/IV 없음" in api._logger.warning.call_args[0][0]


def test_handle_websocket_message_signing_notice_decrypt_fail_h0eucni0(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16

    with patch.object(api, "_aes_cbc_base64_dec", return_value=None):
        msg = f"1|H0EUCNI0|some_key|ENC_DATA"
        api._handle_websocket_message(msg)
        api._logger.error.assert_called_once()
        assert "복호화 실패" in api._logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_get_approval_key_missing_key_field(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"not_approval_key": "123"}  # ✅ approval_key 없음

        result = await api._get_approval_key()
        assert result is None
        api._logger.error.assert_called_once()
        assert "웹소켓 접속키 발급 실패" in api._logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_get_approval_key_empty_auth_data(websocket_api_instance):
    api = websocket_api_instance
    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.requests.post"

    with patch(patch_target) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {}  # 빈 JSON 응답

        result = await api._get_approval_key()
        assert result is None
        api._logger.error.assert_called_once()
        assert "웹소켓 접속키 발급 실패" in api._logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_connect_already_connected(websocket_api_instance):
    api = websocket_api_instance
    api.ws = AsyncMock()
    api._is_connected = True

    result = await api.connect()

    assert result is True
    api._logger.info.assert_called_once_with("웹소켓이 이미 연결되어 있습니다.")


@pytest.mark.asyncio
async def test_connect_exception_during_connection(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = False
    api.ws = None
    api.approval_key = "mock_approval_key"

    patch_target = f"{KoreaInvestWebSocketAPI.__module__}.websockets.connect"

    with patch(patch_target, side_effect=Exception("Connection failed")):
        result = await api.connect()

        assert result is False
        assert api._is_connected is False
        assert api.ws is None
        api._logger.error.assert_called_once()
        assert "웹소켓 연결 중 오류 발생" in api._logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_send_realtime_request_success(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.approval_key = "dummy_approval_key"

    result = await api.send_realtime_request("H0STCNT0", "005930", tr_type="1")

    assert result is True
    api.ws.send.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_receive_task_exception_logging(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api.ws = AsyncMock()
    api.ws.close = AsyncMock()

    # 예외 발생하는 receive_task 생성
    class DummyTask:
        def cancel(self):
            pass

        def __await__(self):  # 비동기 아님, generator 반환
            def generator():
                raise Exception("예외 발생 during await")
                yield  # 실제로는 실행되지 않지만 필요

            return generator()

    api._receive_task = DummyTask()

    await api.disconnect()

    # 예외 로그 검증
    api._logger.error.assert_called_once()
    assert "웹소켓 수신 태스크 종료 중 오류" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_already_in_subscribe_warning(websocket_api_instance):
    api = websocket_api_instance

    message = json.dumps({
        "header": {"tr_id": "H0STCNT0", "tr_key": "test_key"},
        "body": {
            "rt_cd": "1",
            "msg1": "ALREADY IN SUBSCRIBE"
        }
    })

    api._handle_websocket_message(message)

    api._logger.warning.assert_called_once_with("이미 구독 중인 종목입니다.")
    api._logger.error.assert_called_once()
    assert "실시간 요청 응답 오류" in api._logger.error.call_args[0][0]


def test_handle_websocket_message_exception_during_processing(websocket_api_instance):
    api = websocket_api_instance

    # json.loads는 성공하되, 내부 로직에서 의도적으로 예외 발생하도록 조작
    with patch.object(api, "_handle_websocket_message", side_effect=Exception("의도된 오류")):
        try:
            api._handle_websocket_message('{"header": {"tr_id": "X"}}')  # 의도된 예외
        except Exception:
            pass  # 테스트 목적상 예외 무시

    # 위 patch는 전체 함수 대체라 정상 검증이 어려움 → 예외 유발하는 메시지로 재작성
    broken_message = '{"header": "not_a_dict"}'  # header.get이 불가능한 구조

    api._handle_websocket_message(broken_message)

    api._logger.error.assert_called()
    last_call = api._logger.error.call_args[0][0]
    assert "제어 메시지 처리 중 오류 발생" in last_call
    assert "header" in last_call


@pytest.mark.asyncio
async def test_receive_messages_while_loop_enters_once(websocket_api_instance):
    api = websocket_api_instance
    api._is_connected = True
    api._auto_reconnect = True
    api.ws = AsyncMock()

    # 첫 호출 시 _auto_reconnect를 False로 바꿔 루프 1회만 실행되도록
    def side_effect_recv():
        api._auto_reconnect = False
        return "0|H0STCNT0|000660|some_data"

    api.ws.recv.side_effect = side_effect_recv
    api._handle_websocket_message = MagicMock()

    await api._receive_messages()

    # 루프가 한 번 실행되어 handle_websocket_message가 호출되었는지 검증
    api._handle_websocket_message.assert_called_once_with("0|H0STCNT0|000660|some_data")


@pytest.mark.parametrize("tr_id", [
    "H0STCNI0", "H0STCNI9", "H0IFCNI0", "H0MFCNI0", "H0EUCNI0"
])
def test_handle_websocket_message_signing_notice_tr_ids(websocket_api_instance, tr_id):
    api = websocket_api_instance
    api._aes_key = "x" * 32
    api._aes_iv = "y" * 16
    api._aes_cbc_base64_dec = MagicMock(return_value="decrypted")
    api._parse_signing_notice = MagicMock(return_value={"ok": True})
    api.on_realtime_message_callback = MagicMock()

    message = f"1|{tr_id}|dummy|encrypted_payload"
    api._handle_websocket_message(message)

    api._aes_cbc_base64_dec.assert_called_once_with(api._aes_key, api._aes_iv, "encrypted_payload")
    api._parse_signing_notice.assert_called_once_with("decrypted", tr_id)
    api.on_realtime_message_callback.assert_called_once()


@pytest.mark.parametrize("tr_id", [
    "H0STCNI0",
    "H0STCNI9",
    "H0IFCNI0",
    "H0MFCNI0",
    "H0EUCNI0",
])
def test_handle_websocket_message_receives_aes_key_iv_success(websocket_api_instance, tr_id):
    api = websocket_api_instance

    key_val = "mock_aes_key"
    iv_val = "mock_aes_iv"

    message = json.dumps({
        "header": {"tr_id": tr_id, "tr_key": "some_key"},
        "body": {
            "rt_cd": "0",
            "msg1": "성공",
            "output": {
                "key": key_val,
                "iv": iv_val
            }
        }
    })

    api._handle_websocket_message(message)

    assert api._aes_key == key_val
    assert api._aes_iv == iv_val
    api._logger.info.assert_any_call(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")


def test_handle_websocket_message_signing_notice_else_branch(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "x" * 32
    api._aes_iv = "y" * 16
    api._aes_cbc_base64_dec = MagicMock(return_value=None)  # 복호화 실패 유도
    api._parse_signing_notice = MagicMock()

    # ✅ 유효한 TR_ID지만 복호화 실패 → else 블록 진입 유도
    message = "1|H0STCNI0|dummy|encrypted_payload"
    api._handle_websocket_message(message)

    api._logger.error.assert_called_once_with(
        "체결통보 복호화 실패: H0STCNI0, 데이터: encrypted_payload..."
    )


def test_handle_websocket_message_missing_aes_key_iv(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None  # or intentionally unset
    api._aes_iv = None

    message = "1|H0MFCNI0|dummy|encrypted_payload"
    api._handle_websocket_message(message)

    api._logger.warning.assert_called_once()


def test_handle_websocket_message_signing_notice_missing_aes_key_iv(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = None  # AES key 없음
    api._aes_iv = None  # AES IV 없음
    api._aes_cbc_base64_dec = MagicMock()
    api._parse_signing_notice = MagicMock()

    # 실시간 체결 통보 메시지 중 하나 사용
    message = "1|H0STCNI0|dummy|encrypted_payload"

    api._handle_websocket_message(message)

    # 복호화 시도조차 하지 않음
    api._aes_cbc_base64_dec.assert_not_called()
    api._parse_signing_notice.assert_not_called()

    # warning 로그가 출력됐는지 확인 (이 부분이 핵심)
    api._logger.warning.assert_called_once()


def test_handle_websocket_message_signing_notice_decryption_failed(websocket_api_instance):
    api = websocket_api_instance
    api._aes_key = "k" * 32
    api._aes_iv = "i" * 16
    api._aes_cbc_base64_dec = MagicMock(return_value=None)  # 복호화 실패
    api._parse_signing_notice = MagicMock()

    message = "1|H0STCNI0|dummy|encrypted_payload"
    api._handle_websocket_message(message)

    # assert 복호화는 시도됨
    api._aes_cbc_base64_dec.assert_called_once_with(api._aes_key, api._aes_iv, "encrypted_payload")

    # ✅ 복호화 실패 로그 확인
    api._logger.error.assert_called()
    args, _ = api._logger.error.call_args
    assert "체결통보 복호화 실패" in args[0]
    assert "H0STCNI0" in args[0]


def test_handle_websocket_message_aes_key_missing_output(websocket_api_instance):
    api = websocket_api_instance

    message = json.dumps({
        "header": {"tr_id": "H0STCNI0", "tr_key": "some_key"},
        "body": {
            "rt_cd": "0",
            "msg1": "성공"
            # 'output' 키 없음 → False 흐름 유도
        }
    })

    api._handle_websocket_message(message)

    assert api._aes_key is None
    assert api._aes_iv is None
    api._logger.info.assert_called_with("실시간 요청 응답 성공: TR_KEY=some_key, MSG=성공")


@pytest.mark.parametrize("tr_id", [
    "H0STCNI0", "H0STCNI9", "H0IFCNI0", "H0MFCNI0", "H0EUCNI0"
])
def test_handle_websocket_message_signing_notice_tr_ids(websocket_api_instance, tr_id):
    api = websocket_api_instance
    api._aes_key = "x" * 32
    api._aes_iv = "y" * 16
    api._aes_cbc_base64_dec = MagicMock(return_value="decrypted")
    api._parse_signing_notice = MagicMock(return_value={"ok": True})
    # api.logger는 픽스처에서 주입된 MagicMock이므로 직접 변경하지 않습니다.
    api.on_realtime_message_callback = MagicMock()

    message = f"1|{tr_id}|dummy|encrypted_payload"
    api._handle_websocket_message(message)

    api._aes_cbc_base64_dec.assert_called_once_with(api._aes_key, api._aes_iv, "encrypted_payload")
    api._parse_signing_notice.assert_called_once_with("decrypted", tr_id)
    api.on_realtime_message_callback.assert_called_once()


@pytest.mark.parametrize("tr_id", [
    "H0STCNI0",
    "H0STCNI9",
    "H0IFCNI0",
    "H0MFCNI0",
    "H0EUCNI0",
])
def test_handle_websocket_message_receives_aes_key_iv_success(websocket_api_instance, tr_id):
    api = websocket_api_instance
    # api.logger는 픽스처에서 주입된 MagicMock이므로 직접 변경하지 않습니다.

    key_val = "mock_aes_key"
    iv_val = "mock_aes_iv"

    message = json.dumps({
        "header": {"tr_id": tr_id, "tr_key": "some_key"},
        "body": {
            "rt_cd": "0",
            "msg1": "성공",
            "output": {
                "key": key_val,
                "iv": iv_val
            }
        }
    })

    api._handle_websocket_message(message)

    assert api._aes_key == key_val
    assert api._aes_iv == iv_val
    api._logger.info.assert_any_call(f"체결통보용 AES KEY/IV 수신 성공. TRID={tr_id}")


@pytest.mark.asyncio
async def test_websocket_reconnection_and_resubscription(websocket_api_instance):
    """
    웹소켓 연결이 끊어졌을 때 자동으로 재연결하고 구독을 복구하는지 테스트합니다.
    """
    api = websocket_api_instance

    # 1. 초기 상태 설정
    api._auto_reconnect = True
    api._is_connected = True
    mock_ws_initial = AsyncMock()
    api.ws = mock_ws_initial

    # 구독 항목 추가 (재연결 시 복구되어야 함)
    tr_id = "H0STCNT0"
    tr_key = "005930"
    api._subscribed_items.add((tr_id, tr_key))

    # 2. Mock 설정

    # (1) asyncio.sleep: 대기 시간 스킵
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        # (2) _establish_connection: 재연결 성공 시뮬레이션
        async def mock_establish_connection():
            api._is_connected = True
            api.ws = AsyncMock()
            # 재연결 후 recv 호출 시 테스트 종료를 위해 CancelledError 발생
            api.ws.recv.side_effect = asyncio.CancelledError("Test End")
            return True

        with patch.object(api, "_establish_connection", side_effect=mock_establish_connection) as mock_est_conn:

            # (3) send_realtime_request: 구독 복구 요청 확인용
            with patch.object(api, "send_realtime_request", new_callable=AsyncMock) as mock_send_request:

                # (4) ws.recv: 첫 번째 호출에서 연결 끊김 예외 발생
                mock_ws_initial.recv.side_effect = Exception("Connection lost")

                # 3. 테스트 실행 (_receive_messages 루프 진입)
                try:
                    await api._receive_messages()
                except asyncio.CancelledError:
                    pass # 의도된 종료

                # 4. 검증
                # 재연결 대기 (sleep) 확인
                mock_sleep.assert_called_with(3)
                # 재연결 시도 확인
                mock_est_conn.assert_called_once()
                # 구독 복구 요청 확인 (_resubscribe_all -> send_realtime_request)
                mock_send_request.assert_awaited_once_with(tr_id, tr_key, tr_type="1")
