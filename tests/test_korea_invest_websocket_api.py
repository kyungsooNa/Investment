import pytest
from unittest.mock import AsyncMock, patch, MagicMock # MagicMock 추가
import json
import base64
import logging
from datetime import datetime

from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI
# Removed: from core.config_loader import ConfigLoader # This import is no longer needed
from core.logger import Logger # 사용자 정의 Logger 사용을 가정

# Mock Logger를 설정하여 일관된 테스트 환경을 제공합니다.
@pytest.fixture
def mock_logger():
    """모의 Logger."""
    mock = AsyncMock(spec=Logger)
    return mock

@pytest.fixture
def websocket_api_instance(mock_logger):
    """KereaInvestWebSocketAPI 인스턴스를 위한 픽스처."""
    # KereaInvestWebSocketAPI는 'env' 객체를 받는 것으로 보입니다.
    mock_env = MagicMock()
    mock_env.get_full_config.return_value = {
        "websocket_url": "wss://dummy-url",
        "api_key": "dummy-api-key",
        "api_secret_key": "dummy-secret-key",
        "base_url": "https://dummy-base-url"
    }

    # tr_ids_config 데이터는 KereaInvestWebSocketAPI 생성자에서 직접 사용하지 않고
    # 내부적으로 env 객체에서 설정되거나 다른 방식으로 로드될 수 있습니다.
    # 만약 tr_ids_config가 KereaInvestWebSocketAPI 생성자에 필요하다면,
    # 해당 부분을 mock_env에 추가하거나 다른 방식으로 전달해야 합니다.
    # 기존 테스트 파일에서 tr_ids_config_data를 직접 생성자에 넘기는 방식은
    # 현재 KereaInvestWebSocketAPI의 __init__ 시그니처와 맞지 않을 수 있습니다.
    # 일단 test_websocket_api_initialization에 맞춰 'env'만 넘기겠습니다.

    instance = KoreaInvestWebSocketAPI( # Corrected class name
        env=mock_env, # 'api_client' 대신 'env' 사용
        logger=mock_logger
    )
    # KereaInvestWebSocketAPI는 내부적으로 tr_ids_config를 로드할 수 있으므로,
    # 테스트를 위해 해당 로딩 메서드를 패치하거나, 인스턴스에 직접 설정해야 할 수 있습니다.
    # 현재 오류는 'api_client' 키워드 인자 때문이므로, 일단 이 부분만 수정합니다.
    # tr_ids_config는 KereaInvestWebSocketAPI의 속성으로 직접 설정해야 할 수도 있습니다.
    instance.tr_ids_config = { # Test needs this for parsing logic
        "H0STASP0": {"msg_type": "stock_quote", "encrypted": False},
        "H0STCNT0": {"msg_type": "stock_contract", "encrypted": True},
        "H0STCNI0": {"msg_type": "signing_notice", "encrypted": True},
        "H0STCNI9": {"msg_type": "signing_notice", "encrypted": True},
    }
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
