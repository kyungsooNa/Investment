import pytest
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from unittest.mock import MagicMock, patch, mock_open, Mock
from datetime import datetime, timedelta
import pytz
import yaml
import io
import requests
import json


def test_set_trading_mode_switch():
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'is_paper_trading': False,
        'paper_url': 'https://paper-api.com',
        'url': 'https://real-api.com',
        'paper_websocket_url': 'wss://paper-ws',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env.set_trading_mode(True)
    assert env.is_paper_trading is True
    assert env.base_url == 'https://paper-api.com'
    assert env.websocket_url == 'wss://paper-ws'
    logger.info.assert_called_with('거래 모드가 모의투자 환경으로 변경되었습니다.')

def test_set_trading_mode_no_change():
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'is_paper_trading': False,
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env.set_trading_mode(False)
    logger.info.assert_called_with('거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.')

@patch("os.path.exists", return_value=False)
def test_read_token_file_not_exist(mock_exists):
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)
    token = env._read_token_from_file()
    assert token is None
    logger.debug.assert_called_with("토큰 파일이 존재하지 않습니다.")


@patch("api.env.open", new_callable=mock_open)  # 🔸 핵심: 해당 모듈만 patch
@patch("os.path.exists", return_value=True)
def test_read_token_file_valid_yaml(mock_exists, mock_file):
    logger = MagicMock()

    # 준비: 유효한 토큰 데이터 생성
    kst = pytz.timezone('Asia/Seoul')
    valid_date = (datetime.now(kst) + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    base_url = "https://real-api.com"
    token_data = {
        'token': 'abc123',
        'valid-date': valid_date,
        'base-url': base_url
    }

    # YAML 형식 문자열 생성
    yaml_content = yaml.dump(token_data, allow_unicode=True)

    # 파일 객체가 read()가 아니라 직접 파싱되므로 StringIO 사용
    from io import StringIO
    mock_file.return_value.__enter__.return_value = StringIO(yaml_content)

    env = KoreaInvestApiEnv({
        'url': base_url,
        'websocket_url': 'wss://real-ws'
    }, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    assert result is not None
    assert result['token'] == 'abc123'
    assert isinstance(result['valid-date'], datetime)
    assert result['base-url'] == base_url
    logger.debug.assert_called_with("파일에서 읽은 토큰이 유효합니다.")


@patch("api.env.open", new_callable=mock_open, read_data=':')
@patch("os.path.exists", return_value=True)
def test_read_token_file_invalid_yaml(mock_exists, mock_file):
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env._token_file_path = "dummy_token.yaml"

    token = env._read_token_from_file()

    assert token is None
    logger.error.assert_called()


kst = pytz.timezone('Asia/Seoul')

@patch("os.path.exists", return_value=True)
@patch("builtins.open")
def test_read_token_file_valid_yaml(mock_open, mock_exists):
    logger = MagicMock()

    # 유효한 토큰 내용 생성
    valid_date = (datetime.now(kst) + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    base_url = "https://real-api.com"
    token_data = {
        'token': 'abc123',
        'valid-date': valid_date,
        'base-url': base_url
    }
    yaml_content = yaml.dump(token_data, allow_unicode=True)

    # StringIO를 file처럼 열리게 patch
    mock_open.return_value.__enter__.return_value = io.StringIO(yaml_content)

    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # ⚠ 실제 import 경로로 맞춰주세요
    env = KoreaInvestApiEnv({
        'url': base_url,
        'websocket_url': 'wss://real-ws'
    }, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    assert result is not None
    assert result['token'] == 'abc123'
    assert isinstance(result['valid-date'], datetime)
    assert result['base-url'] == base_url
    logger.debug.assert_called_with("파일에서 읽은 토큰이 유효합니다.")


@patch("builtins.open", new_callable=mock_open, read_data="invalid_yaml: :::")
@patch("os.path.exists", return_value=True)
def test_read_token_file_invalid_yaml(mock_exists, mock_file):
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env._token_file_path = "dummy_token.yaml"

    token = env._read_token_from_file()
    assert token is None
    logger.error.assert_called()

@patch.object(KoreaInvestApiEnv, "_read_token_from_file")
@patch.object(KoreaInvestApiEnv, "_request_access_token")
def test_get_access_token_with_valid_token(mock_request_token, mock_read_token):
    logger = MagicMock()

    # 1시간 뒤까지 유효한 토큰
    valid_date = datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(hours=1)
    mock_read_token.return_value = {
        "token": "valid123",
        "valid-date": valid_date,
        "base-url": "https://real-api.com"
    }

    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    token = env.get_access_token()

    assert token == "valid123"
    mock_read_token.assert_called_once()
    mock_request_token.assert_not_called()

@patch.object(KoreaInvestApiEnv, "_read_token_from_file", return_value=None)
@patch.object(KoreaInvestApiEnv, "_request_access_token", return_value="new_token_456")
def test_get_access_token_with_request(mock_request_token, mock_read_token):
    logger = MagicMock()

    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    token = env.get_access_token()

    assert token == "new_token_456"
    mock_read_token.assert_called_once()
    mock_request_token.assert_called_once()

def test_get_access_token_uses_token_from_file():
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv

    logger = MagicMock()
    now = datetime.now(pytz.timezone("Asia/Seoul"))
    future = now + timedelta(hours=1)

    with patch.object(KoreaInvestApiEnv, "_read_token_from_file") as mock_read_token, \
         patch.object(KoreaInvestApiEnv, "_save_token_to_file") as mock_save_token:

        mock_read_token.return_value = {
            "token": "abc123",
            "valid-date": future,
            "base-url": "https://real-api.com"
        }

        env = KoreaInvestApiEnv({
            "url": "https://real-api.com",
            "websocket_url": "wss://real-ws"
        }, logger=logger)

        # 메모리에 토큰 없는 상태
        env.access_token = None
        env.token_expired_at = None

        token = env.get_access_token()

        # ✅ 기대 결과: 파일에서 읽은 토큰 사용
        assert token == "abc123"
        assert env.access_token == "abc123"
        assert env.token_expired_at == future
        logger.info.assert_called_with("파일에서 기존 유효한 토큰 사용.")
        mock_save_token.assert_not_called()

def test_request_access_token_success():
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    import certifi
    import requests

    logger = MagicMock()

    with patch("certifi.where", return_value="/mock/cert.pem"), \
         patch.object(requests.Session, "post") as mock_post, \
         patch.object(KoreaInvestApiEnv, "_save_token_to_file") as mock_save_file:

        # mock된 API 응답
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "newtoken123",
            "expires_in": 3600
        }
        mock_post.return_value = mock_response

        env = KoreaInvestApiEnv({
            'url': 'https://real-api.com',
            'websocket_url': 'wss://real-ws'
        }, logger=logger)

        token = env._request_access_token()

        assert token == "newtoken123"
        assert env.access_token == "newtoken123"
        assert isinstance(env.token_expired_at, datetime)
        mock_save_file.assert_called_once()
        logger.info.assert_called()

def test_request_access_token_invalid_response():
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    import certifi
    import requests

    logger = MagicMock()

    with patch("certifi.where", return_value="/mock/cert.pem"), \
         patch.object(requests.Session, "post") as mock_post:

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # access_token 없음
        mock_post.return_value = mock_response

        env = KoreaInvestApiEnv({
            'url': 'https://real-api.com',
            'websocket_url': 'wss://real-ws'
        }, logger=logger)

        token = env._request_access_token()

        assert token is None
        logger.error.assert_called()
def test_request_access_token_exception():
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
    import certifi
    import requests

    logger = MagicMock()

    with patch("certifi.where", return_value="/mock/cert.pem"), \
         patch.object(requests.Session, "post", side_effect=Exception("mocked error")):

        env = KoreaInvestApiEnv({
            'url': 'https://real-api.com',
            'websocket_url': 'wss://real-ws'
        }, logger=logger)

        token = env._request_access_token()

        assert token is None
        logger.error.assert_called()


def test_set_base_urls_raises_value_error_when_missing_urls():
    logger = MagicMock()

    # config에 필수 URL 정보가 누락된 경우
    config = {
        'url': None,
        'websocket_url': None
    }

    with pytest.raises(ValueError, match="API URL 또는 WebSocket URL이 config.yaml에 올바르게 설정되지 않았습니다."):
        KoreaInvestApiEnv(config, logger=logger)

def test_get_base_headers_without_token():
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env.access_token = None  # 토큰 없음
    headers = env.get_base_headers()

    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == env.my_agent
    assert headers["charset"] == "UTF-8"
    assert "Authorization" not in headers  # ❌ 없음


def test_get_base_headers_with_token():
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env.access_token = "abc123"
    headers = env.get_base_headers()

    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == env.my_agent
    assert headers["charset"] == "UTF-8"
    assert headers["Authorization"] == "Bearer abc123"  # ✅ 있음


def test_get_full_config_in_real_mode():
    config = {
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws',
        'api_key': 'real_key',
        'api_secret_key': 'real_secret',
        'stock_account_number': '123-45',
        'paper_api_key': 'paper_key',
        'paper_api_secret_key': 'paper_secret',
        'paper_stock_account_number': '999-99',
        'tr_ids': {'sample_tr': 'TR001'}
    }
    logger = MagicMock()

    env = KoreaInvestApiEnv(config, logger=logger)
    env.access_token = "token123"
    env.token_expired_at = "2030-01-01"
    env.htsid = "HTS"
    env.custtype = "P"

    full_config = env.get_full_config()

    assert full_config['api_key'] == 'real_key'
    assert full_config['api_secret_key'] == 'real_secret'
    assert full_config['stock_account_number'] == '123-45'
    assert full_config['base_url'] == 'https://real-api.com'
    assert full_config['websocket_url'] == 'wss://real-ws'
    assert full_config['access_token'] == "token123"
    assert full_config['token_expired_at'] == "2030-01-01"
    assert full_config['htsid'] == "HTS"
    assert full_config['custtype'] == "P"
    assert full_config['is_paper_trading'] is False
    assert full_config['tr_ids'] == {'sample_tr': 'TR001'}
    assert full_config['_env_instance'] is env

def test_get_full_config_in_paper_mode():
    config = {
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws',
        'paper_url': 'https://paper-api.com',
        'paper_websocket_url': 'wss://paper-ws',
        'api_key': 'real_key',
        'api_secret_key': 'real_secret',
        'stock_account_number': '123-45',
        'paper_api_key': 'paper_key',
        'paper_api_secret_key': 'paper_secret',
        'paper_stock_account_number': '999-99',
        'tr_ids': {'sample_tr': 'TR001'},
        'is_paper_trading': True
    }
    logger = MagicMock()

    env = KoreaInvestApiEnv(config, logger=logger)
    env.access_token = "token456"
    env.token_expired_at = "2035-12-31"
    env.htsid = "MTS"
    env.custtype = "C"

    full_config = env.get_full_config()

    assert full_config['api_key'] == 'paper_key'
    assert full_config['api_secret_key'] == 'paper_secret'
    assert full_config['stock_account_number'] == '999-99'
    assert full_config['base_url'] == 'https://paper-api.com'
    assert full_config['websocket_url'] == 'wss://paper-ws'
    assert full_config['access_token'] == "token456"
    assert full_config['token_expired_at'] == "2035-12-31"
    assert full_config['htsid'] == "MTS"
    assert full_config['custtype'] == "C"
    assert full_config['is_paper_trading'] is True
    assert full_config['tr_ids'] == {'sample_tr': 'TR001'}
    assert full_config['_env_instance'] is env


def test_get_auth_body_real_mode():
    config = {
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws',
        'api_key': 'real_key',
        'api_secret_key': 'real_secret',
        'stock_account_number': '123-45',
        'paper_api_key': 'paper_key',
        'paper_api_secret_key': 'paper_secret',
        'paper_stock_account_number': '999-99',
        'is_paper_trading': False
    }
    logger = MagicMock()

    env = KoreaInvestApiEnv(config, logger=logger)
    body = env._get_auth_body()

    assert body['grant_type'] == 'client_credentials'
    assert body['appkey'] == 'real_key'
    assert body['appsecret'] == 'real_secret'

def test_get_auth_body_paper_mode():
    config = {
        'paper_url': 'https://paper-api.com',
        'paper_websocket_url': 'wss://paper-ws',
        'api_key': 'real_key',
        'api_secret_key': 'real_secret',
        'stock_account_number': '123-45',
        'paper_api_key': 'paper_key',
        'paper_api_secret_key': 'paper_secret',
        'paper_stock_account_number': '999-99',
        'is_paper_trading': True
    }
    logger = MagicMock()

    env = KoreaInvestApiEnv(config, logger=logger)
    body = env._get_auth_body()

    assert body['grant_type'] == 'client_credentials'
    assert body['appkey'] == 'paper_key'
    assert body['appsecret'] == 'paper_secret'



@patch("builtins.open", new_callable=mock_open)
def test_save_token_to_file_success(mock_file):
    logger = MagicMock()
    config = {
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }
    env = KoreaInvestApiEnv(config, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    token = "test_token"
    expires_at = "2099-01-01 00:00:00"
    base_url = "https://real-api.com"

    env._save_token_to_file(token, expires_at, base_url)

    # 파일이 write 모드로 열렸는지 확인
    mock_file.assert_called_once_with("dummy_token.yaml", 'w', encoding='utf-8')

    # yaml.dump 호출 여부 확인
    handle = mock_file()
    handle.write.assert_called()  # write가 호출되었는지만 확인 (내용까지는 생략 가능)

    logger.info.assert_called_with("토큰 정보를 파일에 저장했습니다: dummy_token.yaml")

@patch("builtins.open", side_effect=IOError("쓰기 실패"))
def test_save_token_to_file_failure(mock_file):
    logger = MagicMock()
    config = {
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }
    env = KoreaInvestApiEnv(config, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    env._save_token_to_file("token", "2025-12-31 23:59:59", "https://real-api.com")

    logger.error.assert_called()
    args, _ = logger.error.call_args
    assert "토큰 파일 저장 실패" in args[0]


FUTURE_DATE = (datetime.now(pytz.timezone("Asia/Seoul")) + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')


@patch("os.path.exists", return_value=True)
@patch("builtins.open")
def test_token_file_base_url_mismatch(mock_open, mock_exists):
    logger = MagicMock()
    wrong_base_url = "https://wrong-api.com"
    token_data = {
        "token": "token123",
        "valid-date": FUTURE_DATE,
        "base-url": wrong_base_url
    }

    # 핵심: open().read()의 결과를 StringIO로 명확하게 지정
    mock_open.return_value.__enter__.return_value = io.StringIO(yaml.dump(token_data))

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    assert result is None
    logger.info.assert_called()



@patch("os.path.exists", return_value=True)
@patch("builtins.open")
def test_token_file_invalid_date_format(mock_open, mock_exists):
    logger = MagicMock()
    invalid_date = "not-a-date"
    token_data = {
        "token": "token123",
        "valid-date": invalid_date,
        "base-url": "https://real-api.com"
    }
    # ✅ 여기에서 mock_file → mock_open 으로 수정
    mock_open.return_value.__enter__.return_value = io.StringIO(yaml.dump(token_data))

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    assert result is None
    logger.error.assert_called()
    assert "토큰 파일의 만료 시간 파싱 오류" in logger.error.call_args[0][0]


kst = pytz.timezone("Asia/Seoul")
PAST_DATE = (datetime.now(kst) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

# 3. 만료된 토큰 검증


@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=mock_open, read_data=yaml.dump({
    "token": "token123",
    "valid-date": PAST_DATE,
    "base-url": "https://real-api.com"
}))
def test_token_file_expired_token(mock_open, mock_exists):
    logger = MagicMock()

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)
    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    assert result is None
    logger.debug.assert_called()


@patch.object(KoreaInvestApiEnv, "_read_token_from_file", return_value=None)
def test_get_access_token_uses_memory_token(mock_read_token):
    logger = MagicMock()

    kst = pytz.timezone("Asia/Seoul")
    future_time = datetime.now(kst) + timedelta(minutes=30)

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)

    # memory에 이미 저장된 유효한 토큰 세팅
    env.access_token = "cached_token_123"
    env.token_expired_at = future_time

    token = env.get_access_token()

    assert token == "cached_token_123"
    logger.info.assert_called()


@patch("requests.Session.post", side_effect=requests.exceptions.RequestException("Connection error"))
def test_request_access_token_network_error(mock_post):
    logger = MagicMock()
    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)

    result = env._request_access_token()

    assert result is None
    logger.error.assert_called()
    assert "토큰 발급 중 네트워크 오류" in logger.error.call_args[0][0]


@patch("requests.Session.post")
def test_request_access_token_json_decode_error(mock_post):
    logger = MagicMock()

    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.text = "Not a JSON response"
    mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "Not a JSON response", 0)
    mock_post.return_value = mock_response

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)

    result = env._request_access_token()

    assert result is None
    logger.error.assert_called()
    assert "토큰 발급 응답 JSON 디코딩 실패" in logger.error.call_args[0][0]


@patch.object(KoreaInvestApiEnv, "_request_access_token", return_value="new_token")
def test_get_access_token_force_new_true(mock_request_token):
    logger = MagicMock()

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)

    result = env.get_access_token(force_new=True)

    assert result == "new_token"
    mock_request_token.assert_called_once()


@patch("os.path.exists", return_value=True)
@patch("builtins.open", new_callable=mock_open, read_data=yaml.dump({
    "valid-date": "2099-12-31 23:59:59",
    "base-url": "https://real-api.com"
}))
def test_read_token_from_file_missing_keys(mock_file, mock_exists):
    logger = MagicMock()

    env = KoreaInvestApiEnv({
        "url": "https://real-api.com",
        "websocket_url": "wss://real-ws"
    }, logger=logger)

    env._token_file_path = "dummy_token.yaml"

    result = env._read_token_from_file()

    # 필수 키(token)가 없으므로 None 반환
    assert result is None
    # 로그도 발생해야 함
    logger.debug.assert_called_with("파일에서 유효한 토큰을 찾을 수 없거나 만료되었습니다.")