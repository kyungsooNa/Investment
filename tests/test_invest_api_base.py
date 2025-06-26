import pytest
import json
from unittest.mock import MagicMock, patch
from api.invest_api_base import _KoreaInvestAPIBase
import requests
import logging


@pytest.mark.asyncio
async def test_log_request_exception_cases(caplog):
    api = _KoreaInvestAPIBase("http://test", {}, {}, logger=None)

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
    api = _KoreaInvestAPIBase("http://test", {}, {}, logger=None)
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(api._session, "post", lambda *a, **k: mock_response)
    result = await api._execute_request("POST", "http://test", {}, {"x": "y"})
    assert result.status_code == 200

@pytest.mark.asyncio
async def test_execute_request_invalid_method():
    api = _KoreaInvestAPIBase("http://test", {}, {}, logger=None)
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
    api = _KoreaInvestAPIBase("http://test", headers, {}, logger=None)

    with caplog.at_level("DEBUG"):
        api._log_headers()

    assert "*** UnicodeEncodeError ***" in caplog.text


@pytest.mark.asyncio
async def test_call_api_with_http_error_status(caplog):
    """status_code != 200"""
    response_mock = MagicMock()
    response_mock.status_code = 500
    response_mock.text = "Internal Server Error"

    api = _KoreaInvestAPIBase("http://test", {}, {}, logger=None)
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

    api = _KoreaInvestAPIBase("http://test", {}, {}, logger=None)
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
    api = _KoreaInvestAPIBase("http://test", {}, config, logger=None)
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
    import api.invest_api_base as base_module
    logger_name = "api.invest_api_base"

    # logger 직접 설정
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True  # caplog 연결

    # 주입
    api = base_module._KoreaInvestAPIBase("http://test", {}, {}, logger=logger)
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


