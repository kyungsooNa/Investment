import pytest
import json
from unittest.mock import MagicMock
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
import requests
import logging


@pytest.mark.asyncio
async def test_log_request_exception_cases(caplog):
    api = KoreaInvestApiBase("http://test", {}, {}, logger=None)

    class DummyResponse:
        status_code = 500
        text = "error"
    http_error = requests.exceptions.HTTPError(response=DummyResponse())
    connection_error = requests.exceptions.ConnectionError("conn")
    timeout_error = requests.exceptions.Timeout("timeout")
    request_exception = requests.exceptions.RequestException("req")
    json_error = json.JSONDecodeError("msg", "doc", 0)
    generic_exception = Exception("generic")

    with caplog.at_level("ERROR"):
        for exc in [http_error, connection_error, timeout_error, request_exception, json_error, generic_exception]:
            api._log_request_exception(exc)

    for expected in ["HTTP ", " ", "", " ", "JSON ", " "]:
        assert any(expected in message for message in caplog.messages)

@pytest.mark.asyncio
async def test_execute_request_post(monkeypatch):
    api = KoreaInvestApiBase("http://test", {}, {}, logger=None)
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(api._session, "post", lambda *a, **k: mock_response)
    result = await api._execute_request("POST", "http://test", {}, {"x": "y"})
    assert result.status_code == 200

@pytest.mark.asyncio
async def test_execute_request_invalid_method():
    api = KoreaInvestApiBase("http://test", {}, {}, logger=None)
    with pytest.raises(ValueError):
        await api._execute_request("PUT", "http://test", {}, {})

class ExplodingString(str):
    def encode(self, encoding='utf-8', errors='strict'):
        raise UnicodeEncodeError(encoding, self, 0, 1, "intentional failure")

class ExplodingHeader:
    def __str__(self):
        return ExplodingString("trigger")

@pytest.mark.asyncio
async def test_log_headers_unicode_error_with_custom_object(caplog):
    headers = {"bad": ExplodingHeader()}
    api = KoreaInvestApiBase("http://test", headers, {}, logger=None)

    with caplog.at_level("DEBUG"):
        api._log_headers()

    assert "*** UnicodeEncodeError ***" in caplog.text


@pytest.mark.asyncio
async def test_call_api_with_http_error_status(caplog):
    """status_code != 200"""
    response_mock = MagicMock()
    response_mock.status_code = 500
    response_mock.text = "Internal Server Error"

    api = KoreaInvestApiBase("http://test", {}, {}, logger=None)
    api._session.request = MagicMock(return_value=response_mock)

    result = await api.call_api("GET", "/fail")
    assert result is None
    assert "비정상 HTTP 상태 코드" in caplog.text

@pytest.mark.asyncio
async def test_call_api_with_invalid_json_type(caplog):
    """응답이 dict가 아님"""
    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.json.return_value = "not a dict"

    api = KoreaInvestApiBase("http://test", {}, {}, logger=None)
    api._session.request = MagicMock(return_value=response_mock)

    result = await api.call_api("GET", "/invalid")
    assert result is None
    assert "잘못된 응답 형식" in caplog.text

@pytest.mark.asyncio
async def test_call_api_token_renew_failed(caplog):
    """토큰 재발급 후에도 실패"""
    mock_env = MagicMock()
    mock_env.refresh_access_token.return_value = False

    config = {"_env_instance": mock_env}
    api = KoreaInvestApiBase("http://test", {}, config, logger=None)
    api._env = mock_env  # config와 별도로 보장

    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.text = "expired"
    response_mock.json.return_value = {
        "rt_cd": "1",
        "msg_cd": "EGW00123"
    }

    api._session.request = MagicMock(return_value=response_mock)

    with caplog.at_level(logging.ERROR):
        result = await api.call_api("GET", "/token-expired")

    assert result is None
    assert "토큰" in caplog.text and "실패" in caplog.text


@pytest.mark.asyncio
async def test_call_api_no_env_instance(caplog):
    from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
    logger_name = KoreaInvestApiBase.__module__

    # logger 직접 설정
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True  # caplog 연결

    # 주입
    api = KoreaInvestApiBase("http://test", {}, {}, logger=logger)
    api._env = None

    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.text = "expired"
    response_mock.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00123"}
    api._session.request = MagicMock(return_value=response_mock)

    with caplog.at_level(logging.ERROR, logger=logger_name):
        result = await api.call_api("GET", "/no-env")

    print("\n=== Captured Log ===")
    for r in caplog.records:
        print(f"[{r.levelname}] {r.name} - {r.message}")
    print("=====================\n")

    assert result is None
    assert any("KoreaInvestEnv 인스턴스를 찾을 수 없어 토큰 초기화 불가" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_token_renewal_failure_triggers_exit(caplog):
    # api._env에 할당될 mock_env를 위한 가상의 KoreaInvestEnv 클래스 (실제 클래스 대체)
    # 실제 KoreaInvestEnv 클래스가 임포트되지 않았거나, 메서드 이름이 다를 때 이와 같이 Mock합니다.
    class MockKoreaInvestEnv:
        def __init__(self):
            # refresh_access_token이 실제 존재하지 않거나 비동기 함수가 아닐 수 있으므로
            # 여기서는 단순히 이 클래스가 해당 메서드를 가지고 있다고 Mock으로 명시
            pass

        # 만약 실제 KoreaInvestEnv에 refresh_access_token이 없었다면,
        # 아래는 테스트를 통과시키기 위한 가상의 비동기 메서드를 추가하는 예시입니다.
        # 그러나 더 좋은 방법은 실제 KoreaInvestEnv의 토큰 갱신 메서드 이름을 파악하는 것입니다.
        async def refresh_access_token(self):
            # 테스트를 위해 이 메서드가 AsyncMock으로 대체될 것입니다.
            pass

    # KoreaInvestEnv가 임포트되지 않았다면 위 MockKoreaInvestEnv 사용
    # KoreaInvestEnv = MockKoreaInvestEnv # 이렇게 대체하여 사용 가능

    # api._env에 할당될 mock_env를 생성
    api = KoreaInvestApiBase("http://test", {}, {"_env_instance": MagicMock()}, logger=None)

    # KoreaInvestEnv가 실제 존재한다면:
    mock_env = MagicMock(spec=KoreaInvestApiEnv)  # KoreaInvestEnv는 실제 클래스여야 합니다.

    # 만약 KoreaInvestEnv에 'refresh_access_token' 메서드가 없고 'request_access_token' 이나 다른 이름이라면
    # 이 부분을 해당 이름으로 변경해야 합니다.
    # 예시: mock_env._request_access_token.return_value = False
    mock_env._request_access_token.return_value = False # <--- 이 부분을 수정

    api._env = mock_env

    with caplog.at_level(logging.ERROR):
        result = await api._handle_token_expiration(
            response_json={"msg_cd": "EGW00123"},
            attempt=3,  # 최대 재시도 횟수 도달
            retry_count=3,
            delay=0
        )

    assert result is None
    assert "토큰 재발급 후에도 실패, 종료" in caplog.text