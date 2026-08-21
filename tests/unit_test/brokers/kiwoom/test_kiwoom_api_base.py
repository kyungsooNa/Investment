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
