import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
import requests

class DummyAPI(KoreaInvestApiBase):
    # call_api를 호출 가능하도록 래핑
    async def call_api_wrapper(self, *args, **kwargs):
        return await self.call_api(*args, **kwargs)

@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
    base_url = "https://dummy-base"
    headers = {"Authorization": "Bearer dummy"}
    config = {
        "tr_ids": {},
        "_env_instance": None,
    }
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.ERROR)

    api = DummyAPI(base_url, headers, config, MagicMock(), logger)

    # 항상 500 + 초당 거래건수 초과 응답만 반환
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"msg1": "초당 거래건수를 초과하였습니다."}
    mock_response.text = '{"msg1":"초당 거래건수를 초과하였습니다."}'
    mock_response.raise_for_status = MagicMock()

    api._session.get = MagicMock(return_value=mock_response)

    with caplog.at_level(logging.ERROR):
        result = await api.call_api('GET', '/dummy-path', retry_count=2, delay=0.01)

    assert result is None

    # ✅ 로그 메시지 수정에 맞춰 assertion 변경
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("모든 재시도 실패" in rec.message for rec in errors)


@pytest.mark.asyncio
async def testcall_api_retry_exceed_failure(caplog):
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
        result = await api.call_api('GET', '/dummy-path', retry_count=2, delay=0)

    # 실패시 None 리턴
    assert result is None

    # 오류 로그가 기록됐는지 확인
    errors = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert any("모든 재시도 실패" in rec.message for rec in errors)


@pytest.mark.asyncio
async def testcall_api_success():
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
@patch("asyncio.sleep", new_callable=AsyncMock)  # <-- sleep patch
async def testcall_api_retry_on_429(monkeypatch):
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
@patch("asyncio.sleep", new_callable=AsyncMock)  # <-- sleep patch
async def testcall_api_retry_on_500_rate_limit(monkeypatch):
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
async def testcall_api_token_expired_retry():
    class MockTokenManager:
        def __init__(self):
            self.invalidated = False

        def invalidate_token(self):
            self.invalidated = True

    token_manager = MockTokenManager()

    dummy = DummyAPI(
        base_url="https://mock-base",
        headers={},
        config={"_env_instance": MagicMock()},  # _config is not None
        token_manager=token_manager,
        logger=MagicMock()
    )

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

    result = await dummy.call_api('GET', '/token_expired', retry_count=5, delay=0.01)

    assert result == {"success": True}
    assert token_manager.invalidated is True  # ✅ token_manager.invalidate_token()이 호출되어야 함



@pytest.mark.asyncio
async def testcall_api_http_error(monkeypatch):
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
async def testcall_api_connection_error(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())

    def mock_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection failed")

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/conn_err')
    assert result is None

@pytest.mark.asyncio
async def testcall_api_timeout(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())

    def mock_get(*args, **kwargs):
        raise requests.exceptions.Timeout("Timeout error")

    dummy._session.get = MagicMock(side_effect=mock_get)

    result = await dummy.call_api_wrapper('GET', '/timeout')
    assert result is None

@pytest.mark.asyncio
async def testcall_api_json_decode_error(monkeypatch):
    dummy = DummyAPI("https://mock-base", {}, {}, MagicMock())
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "not json"
    resp.json.side_effect = ValueError("JSON decode error")
    resp.raise_for_status.return_value = None

    dummy._session.get = MagicMock(return_value=resp)

    result = await dummy.call_api_wrapper('GET', '/json_error')
    assert result is None
