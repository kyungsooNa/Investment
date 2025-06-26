import pytest
import json
from unittest.mock import MagicMock
from api.invest_api_base import _KoreaInvestAPIBase
import requests

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

