from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from brokers.kiwoom.kiwoom_api_base import KiwoomApiBase
from common.types import ErrorCode


def _mock_env():
    env = MagicMock()
    env.get_access_token = AsyncMock(return_value="access-token")
    env.active_config = {
        "api_key": "app-key",
        "api_secret_key": "secret-key",
    }
    return env


@pytest.mark.asyncio
async def test_call_api_adds_kiwoom_headers_and_wraps_success():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = '{"return_code":0,"output":{"price":"70000"}}'
    response.raise_for_status.return_value = None
    response.json.return_value = {"return_code": 0, "output": {"price": "70000"}}
    session.post.return_value = response

    api = KiwoomApiBase(
        env=_mock_env(),
        logger=MagicMock(),
        market_clock=AsyncMock(),
        async_client=session,
    )

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={"stk_cd": "005930"})

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["output"]["price"] == "70000"
    _, kwargs = session.post.call_args
    assert kwargs["headers"]["authorization"] == "Bearer access-token"
    assert kwargs["headers"]["api-id"] == "ka10001"
    assert kwargs["headers"]["Content-Type"] == "application/json;charset=UTF-8"


@pytest.mark.asyncio
async def test_call_api_converts_business_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = '{"return_code":1,"return_msg":"bad request"}'
    response.raise_for_status.return_value = None
    response.json.return_value = {"return_code": 1, "return_msg": "bad request"}
    session.post.return_value = response

    api = KiwoomApiBase(
        env=_mock_env(),
        logger=MagicMock(),
        market_clock=AsyncMock(),
        async_client=session,
    )

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "bad request" in result.msg1


@pytest.mark.asyncio
async def test_call_api_converts_network_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    session.post.side_effect = httpx.RequestError("connect failed")

    api = KiwoomApiBase(
        env=_mock_env(),
        logger=MagicMock(),
        market_clock=AsyncMock(),
        async_client=session,
    )

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.NETWORK_ERROR.value
    assert "connect failed" in result.msg1


def _api(session, logger=None):
    return KiwoomApiBase(
        env=_mock_env(),
        logger=logger if logger is not None else MagicMock(),
        market_clock=AsyncMock(),
        async_client=session,
    )


def test_url_keeps_absolute_urls_and_prefixes_relative_paths():
    env = _mock_env()
    env.get_base_url.return_value = "https://api.kiwoom.com"
    api = KiwoomApiBase(env=env, logger=MagicMock(), async_client=AsyncMock(spec=httpx.AsyncClient))

    assert api.url("https://mock.kiwoom.com/api") == "https://mock.kiwoom.com/api"
    assert api.url("http://mock.kiwoom.com/api") == "http://mock.kiwoom.com/api"
    assert api.url("/api/dostk/stkinfo") == "https://api.kiwoom.com/api/dostk/stkinfo"


def test_creates_own_async_client_when_none_injected():
    api = KiwoomApiBase(env=_mock_env(), logger=MagicMock())

    assert isinstance(api._async_session, httpx.AsyncClient)


@pytest.mark.asyncio
async def test_close_session_closes_underlying_client():
    session = AsyncMock(spec=httpx.AsyncClient)
    api = _api(session)

    await api.close_session()

    session.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_request_passes_params_and_headers():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"return_code": "0000", "output": []}
    session.get.return_value = response
    api = _api(session)

    result = await api.call_api(
        "get", "/api/dostk/stkinfo", api_id="ka10001", params={"stk_cd": "005930"}
    )

    assert result.rt_cd == ErrorCode.SUCCESS.value
    _, kwargs = session.get.call_args
    assert kwargs["params"] == {"stk_cd": "005930"}
    assert kwargs["headers"]["api-id"] == "ka10001"


@pytest.mark.asyncio
async def test_unsupported_http_method_returns_api_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    logger = MagicMock()
    api = _api(session, logger=logger)

    result = await api.call_api("PUT", "/api/dostk/stkinfo", api_id="ka10001")

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "PUT" in result.msg1
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_http_status_error_maps_to_network_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401)
    )
    session.post.return_value = response
    api = _api(session)

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.NETWORK_ERROR.value
    assert "401" in result.msg1


@pytest.mark.asyncio
async def test_undecodable_body_maps_to_parsing_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    api = _api(session)

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result.msg1 == "JSON Decode Error"


@pytest.mark.asyncio
async def test_non_dict_payload_maps_to_parsing_error():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = ["not", "a", "dict"]
    session.post.return_value = response
    api = _api(session)

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result.msg1 == "Response is not a dict"


@pytest.mark.asyncio
async def test_business_error_falls_back_to_message_field():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"return_code": 9, "message": "token expired"}
    session.post.return_value = response
    api = _api(session)

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert result.msg1 == "token expired"


@pytest.mark.asyncio
async def test_payload_without_return_code_is_treated_as_success():
    session = AsyncMock(spec=httpx.AsyncClient)
    response = MagicMock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": {"price": "70000"}}
    session.post.return_value = response
    api = _api(session)

    result = await api.call_api("POST", "/api/dostk/stkinfo", api_id="ka10001", data={})

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["output"]["price"] == "70000"
