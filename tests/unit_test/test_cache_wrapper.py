# test_cache_wrapper.py

import pytest
from unittest.mock import MagicMock
from core.cache_wrapper import cache_wrap_client, CACHED_METHODS

class DummyApiClient:
    async def get_data(self, x):
        return f"result-{x}"

    async def bypass_data(self, y):
        return f"no_cache-{y}"

@pytest.mark.asyncio
async def test_cache_wrapper_hit_and_miss():
    logger = MagicMock()
    client = DummyApiClient()
    CACHED_METHODS.add("get_data")
    client = cache_wrap_client(client, logger)

    result1 = await client.get_data(1)
    result2 = await client.get_data(1)

    assert result1 == "result-1"
    assert result2 == "result-1"

    assert any("Cache MISS" in call.args[0] for call in logger.debug.call_args_list)
    assert any("Cache HIT" in call.args[0] for call in logger.debug.call_args_list)

@pytest.mark.asyncio
async def test_cache_wrapper_bypass_for_non_cached_method():
    logger = MagicMock()
    client = DummyApiClient()
    client = cache_wrap_client(client, logger)

    result = await client.bypass_data(9)
    assert result == "no_cache-9"

    assert any("Bypass" in call.args[0] for call in logger.debug.call_args_list)

def test_cache_wrapper_dir_includes_wrapped_methods():
    client = DummyApiClient()
    logger = MagicMock()
    wrapped_client = cache_wrap_client(client, logger)

    dir_list = dir(wrapped_client)

    # DummyApiClient에 있는 메서드들이 포함되어야 함
    assert "get_data" in dir_list
    assert "bypass_data" in dir_list

    # 내부 속성도 포함되어야 함
    assert "_client" in dir_list
    assert "_logger" in dir_list
