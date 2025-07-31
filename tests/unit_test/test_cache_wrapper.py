# test_cache_wrapper.py

import pytest
import json
from unittest.mock import MagicMock
from core.cache.cache_wrapper import cache_wrap_client


class DummyApiClient:
    async def get_data(self, x):
        return f"result-{x}"

    async def bypass_data(self, y):
        return f"no_cache-{y}"

@pytest.mark.asyncio
async def test_cache_wrapper_hit_and_miss():
    logger = MagicMock()
    client = DummyApiClient()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False


    wrapped = cache_wrap_client(client, logger, time_manager, lambda: "TEST")  # 한 번만 래핑

    result1 = await wrapped.get_data(1)
    result2 = await wrapped.get_data(1)

    assert result1 == "result-1"
    assert result2 == "result-1"

    assert any("Memory Cache MISS" in call.args[0] for call in logger.debug.call_args_list)
    assert any("File Cache MISS" in call.args[0] for call in logger.debug.call_args_list)
    assert any("Memory cache HIT" in call.args[0] for call in logger.debug.call_args_list)

@pytest.mark.asyncio
async def test_cache_wrapper_bypass_for_non_cached_method():
    logger = MagicMock()
    client = DummyApiClient()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    client = cache_wrap_client(client, logger, time_manager, lambda: "TEST")

    result = await client.bypass_data(9)
    assert result == "no_cache-9"

    assert any("Bypass" in call.args[0] for call in logger.debug.call_args_list)

def test_cache_wrapper_dir_includes_wrapped_methods():
    client = DummyApiClient()
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped_client = cache_wrap_client(client, logger,time_manager, lambda: "TEST")

    dir_list = dir(wrapped_client)

    # DummyApiClient에 있는 메서드들이 포함되어야 함
    assert "get_data" in dir_list
    assert "bypass_data" in dir_list

    # 내부 속성도 포함되어야 함
    assert "_client" in dir_list
    assert "_logger" in dir_list

@pytest.mark.asyncio
async def test_cache_wrapper_key_arg_str_empty_skip_append():
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    client = DummyApiClient()
    wrapped = cache_wrap_client(client, logger, time_manager, lambda: "TEST")

    # ✅ 공백 문자열을 인자로 넘겨 arg_str이 비어 key_parts.append() 안 타도록 유도
    result = await wrapped.get_data("")
    assert result == "result-"

    # ✅ logger.debug 메시지를 기반으로 key_parts에 arg_str이 포함되지 않았는지 검증
    debug_msgs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("TEST_get_data" in msg for msg in debug_msgs)



@pytest.mark.asyncio
async def test_client_with_cache_file_save(tmp_path):
    from core.cache.cache_manager import CacheManager

    # 설정
    CacheManager._instance = None
    manager = CacheManager()
    manager._base_dir = str(tmp_path)
    manager.clear()

    client = DummyApiClient()

    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False  # => 파일 저장 조건

    wrapped = cache_wrap_client(client, logger, time_manager, lambda: "REAL")

    result = await wrapped.get_data(7)
    assert result == {"foo": 7}

    expected_file = tmp_path / "REAL_get_data_7.json"
    assert expected_file.exists()

    with open(expected_file, encoding="utf-8") as f:
        payload = json.load(f)
        assert payload["data"]["foo"] == 7

