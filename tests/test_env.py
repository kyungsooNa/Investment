import pytest
from api.env import KoreaInvestEnv
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta
import pytz
import yaml
import io


def test_set_trading_mode_switch():
    logger = MagicMock()
    env = KoreaInvestEnv({
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
    env = KoreaInvestEnv({
        'is_paper_trading': False,
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env.set_trading_mode(False)
    logger.info.assert_called_with('거래 모드가 이미 실전투자 환경으로 설정되어 있습니다.')

@patch("os.path.exists", return_value=False)
def test_read_token_file_not_exist(mock_exists):
    logger = MagicMock()
    env = KoreaInvestEnv({
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

    env = KoreaInvestEnv({
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
    env = KoreaInvestEnv({
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

    from api.env import KoreaInvestEnv  # ⚠ 실제 import 경로로 맞춰주세요
    env = KoreaInvestEnv({
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
    env = KoreaInvestEnv({
        'url': 'https://real-api.com',
        'websocket_url': 'wss://real-ws'
    }, logger=logger)

    env._token_file_path = "dummy_token.yaml"

    token = env._read_token_from_file()
    assert token is None
    logger.error.assert_called()

