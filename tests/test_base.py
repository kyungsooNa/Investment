import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.base import _KoreaInvestAPIBase
import requests
from api.env import KoreaInvestEnv

class DummyAPI(_KoreaInvestAPIBase):
    # _call_api를 호출 가능하도록 래핑
    async def call_api_wrapper(self, *args, **kwargs):
        return await self._call_api(*args, **kwargs)

@pytest.mark.asyncio
async def test_call_api_retry_on_rate_limit(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")

    api = DummyAPI(base_url, headers, config, logger)

    # 응답 객체 모킹 (500 + 초당 거래건수 초과)
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    # requests.Session.get 모킹 (첫 2회는 실패, 3회째는 정상)
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"rt_cd": "0", "output": {"data": "success"}}
    success_response.text = '{"rt_cd":"0","output":{"data":"success"}}'
    success_response.raise_for_status = MagicMock()

    call_count = 0
    def side_effect_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return mock_response
        else:
            return success_response

    api._session.get = MagicMock(side_effect=side_effect_get)

    with caplog.at_level(logging.WARNING):
        result = await api._call_api('GET', '/dummy-path', retry_count=3, delay=0.01)

    # 정상 응답이 리턴되는지 확인
    assert result == {"rt_cd": "0", "output": {"data": "success"}}

    # 로그 레벨이 WARNING인 로그가 하나 이상 찍혔는지 확인
    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert len(warnings) > 0

    # 총 3회 호출되었는지 확인
    assert call_count == 3

@pytest.mark.asyncio
async def test_call_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")

    api = DummyAPI(base_url, headers, config, logger)

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    api._session.get = MagicMock(return_value=mock_response)

    with caplog.at_level(logging.ERROR):
        result = await api._call_api('GET', '/dummy-path', retry_count=2, delay=0.01)

    # 실패시 None 리턴
    assert result is None

    # 오류 로그가 기록됐는지 확인
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("재시도 횟수 초과" in rec.message for rec in errors)

@pytest.mark.asyncio
async def test_call_api_success():
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    dummy._session.get = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"key":"value"}'
    mock_response.json.return_value = {"key": "value"}
    mock_response.raise_for_status.return_value = None
    dummy._session.get.return_value = mock_response

    result = await dummy.call_api_wrapper('GET', '/test')
    assert result == {"key": "value"}

@pytest.mark.asyncio
async def test_call_api_retry_on_429(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    responses = []

    # 첫 2번 429 반환, 3번째 200 반환
    def mock_get(*args, **kwargs):
        resp = MagicMock()
        if len(responses) < 2:
            resp.status_code = 429
            resp.text = "Too Many Requests"
            resp.json.return_value = {}
        else:
            resp.status_code = 200
            resp.text = '{"success":true}'
            resp.json.return_value = {"success": True}
        resp.raise_for_status.return_value = None
        responses.append(resp)
        return resp

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/retry')
    assert result == {"success": True}
    assert len(responses) == 3

@pytest.mark.asyncio
async def test_call_api_retry_on_500_rate_limit(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    responses = []

    def mock_get(*args, **kwargs):
        resp = MagicMock()
        if len(responses) < 2:
            resp.status_code = 500
            resp.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
            resp.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
        else:
            resp.status_code = 200
            resp.text = '{"success":true}'
            resp.json.return_value = {"success": True}
        resp.raise_for_status.return_value = None
        responses.append(resp)
        return resp

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/retry500')
    assert result == {"success": True}
    assert len(responses) == 3

@pytest.mark.asyncio
async def test_call_api_token_expired_retry(monkeypatch):
    dummy_env = MagicMock(spec=KoreaInvestEnv)
    dummy_env.access_token = "oldtoken"
    dummy_env.token_expired_at = 12345

    dummy = DummyAPI("https://mock-base", {}, {"_env_instance": dummy_env}, MagicMock())
    dummy._env = dummy_env

    responses = []

    def mock_get(*args, **kwargs):
        resp = MagicMock()
        if len(responses) < 2:
            resp.status_code = 200
            resp.text = '{"rt_cd":"1","msg_cd":"EGW00123"}'
            resp.json.return_value = {"rt_cd": "1", "msg_cd": "EGW00123"}
        else:
            resp.status_code = 200
            resp.text = '{"success":true}'
            resp.json.return_value = {"success": True}
        resp.raise_for_status.return_value = None
        responses.append(resp)
        return resp

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy._call_api('GET', '/token_expired', retry_count=10, delay=0.01)

    assert result == {"success": True}
    assert dummy_env.access_token is None
    assert dummy_env.token_expired_at is None
    assert len(responses) == 3

@pytest.mark.asyncio
async def test_call_api_http_error(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "Bad Request"
    http_error = requests.exceptions.HTTPError(response=resp)

    def mock_get(*args, **kwargs):
        raise http_error

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/http_error')
    assert result is None

@pytest.mark.asyncio
async def test_call_api_connection_error(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())

    def mock_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection failed")

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/conn_err')
    assert result is None

@pytest.mark.asyncio
async def test_call_api_timeout(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())

    def mock_get(*args, **kwargs):
        raise requests.exceptions.Timeout("Timeout error")

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/timeout')
    assert result is None

@pytest.mark.asyncio
async def test_call_api_json_decode_error(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "not json"
    resp.json.side_effect = ValueError("JSON decode error")
    resp.raise_for_status.return_value = None

    dummy._session.get = MagicMock(return_value=resp)

    result = await dummy.call_api_wrapper('GET', '/json_error')
    assert result is None
