# test_cache_wrapper.py

import pytest
import json
import pytz
from unittest.mock import MagicMock
from core.cache.cache_wrapper import cache_wrap_client
from core.cache.cache_manager import CacheManager
from datetime import datetime, timedelta
from unittest.mock import ANY

fake_time = (datetime.now() - timedelta(days=1)).isoformat()


class DummyApiClient:
    async def get_data(self, x):
        return f"result-{x}"

    async def bypass_data(self, y):
        return f"no_cache-{y}"


@pytest.mark.asyncio
async def test_cache_wrapper_hit_and_miss(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    # KST 타임존 aware datetime
    seoul_tz = pytz.timezone("Asia/Seoul")
    now = seoul_tz.localize(datetime.now())
    close_time = now - timedelta(minutes=5)

    time_manager.get_market_close_time.return_value = close_time
    time_manager.get_current_kst_time.return_value = now
    time_manager.market_timezone = seoul_tz

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    # 최초 호출 → 캐시 없음
    result1 = await wrapped.get_data(1)
    # 두 번째 호출 → 캐시 HIT 기대
    result2 = await wrapped.get_data(1)

    # ✅ 결과 값 검증
    assert result1 == "result-1"
    assert result2 == "result-1"

    # ✅ 로그 메시지 검증
    debug_logs = [call.args[0] for call in logger.debug.call_args_list]

    assert any("Memory Cache MISS" in msg for msg in debug_logs)
    assert any("File Cache MISS" in msg for msg in debug_logs)
    assert any("Memory Cache HIT" in msg for msg in debug_logs)

@pytest.mark.asyncio
async def test_cache_wrapper_expired_cache_removal(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    # ✅ 실제 datetime 객체로 설정
    seoul_tz = pytz.timezone("Asia/Seoul")
    now = seoul_tz.localize(datetime.now())
    close_time = now - timedelta(minutes=5)

    time_manager.get_market_close_time.return_value = close_time
    time_manager.get_current_kst_time.return_value = now
    time_manager.market_timezone = seoul_tz

    # 👉 시장은 닫혀 있고, 캐시 만료 여부를 확인할 수 있게 구성
    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config
    )

    # 1. 최초 호출 → 캐시 저장
    result1 = await wrapped.get_data(1)
    assert result1 == "result-1"

    # 2. 파일 직접 열어서 timestamp를 하루 전으로 조작
    key = "TEST_get_data_1"
    file_path = cache_manager.file_cache._get_path(key)
    with open(file_path, "r+", encoding="utf-8") as f:
        payload = json.load(f)
        payload["timestamp"] = (datetime.now() - timedelta(days=1)).isoformat()

        f.seek(0)
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.truncate()

    # ✅ 메모리 캐시 제거 → 파일 캐시를 다시 조회하게 유도
    wrapped._cache.memory_cache.clear()

    # 3. 재호출 → 만료되어 새로 생성되어야 함
    result2 = await wrapped.get_data(1)
    assert result2 == "result-1"

    debug_logs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("File Cache 무시 (만료됨)" in log for log in debug_logs)
    assert any("File cache 삭제됨" in log for log in debug_logs)


@pytest.mark.asyncio
async def test_cache_wrapper_bypass_for_non_cached_method(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    result = await wrapped.bypass_data(9)
    assert result == "no_cache-9"

    assert any("Bypass" in call.args[0] for call in logger.debug.call_args_list)


def test_cache_wrapper_dir_includes_wrapped_methods(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    dir_list = dir(wrapped)

    # DummyApiClient에 있는 메서드들이 포함되어야 함
    assert "get_data" in dir_list
    assert "bypass_data" in dir_list

    # 내부 속성도 포함되어야 함
    assert "_client" in dir_list
    assert "_logger" in dir_list


@pytest.mark.asyncio
async def test_cache_wrapper_key_arg_str_empty_skip_append(cache_manager, test_cache_config):
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=test_cache_config  # ✅ 반드시 함께 전달해야 함
    )

    # ✅ 공백 문자열을 인자로 넘겨 arg_str이 비어 key_parts.append() 안 타도록 유도
    result = await wrapped.get_data("")
    assert result == "result-"

    # ✅ logger.debug 메시지를 기반으로 key_parts에 arg_str이 포함되지 않았는지 검증
    debug_msgs = [call.args[0] for call in logger.debug.call_args_list]
    assert any("TEST_get_data" in msg for msg in debug_msgs)


@pytest.mark.asyncio
async def test_client_with_cache_file_save(cache_manager, test_cache_config, tmp_path):
    base_dir = tmp_path
    config = {
        "cache": {
            "base_dir": str(base_dir),
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False  # => 파일 저장 조건

    wrapped = cache_wrap_client(
        api_client=DummyApiClient(),
        logger=logger,
        time_manager=time_manager,
        mode_getter=lambda: "TEST",
        cache_manager=cache_manager,
        config=config  # ✅ 반드시 함께 전달해야 함
    )

    data = 7
    result = await wrapped.get_data(data)
    assert result == f"result-{data}"

    expected_file = tmp_path / "TEST_get_data_7.json"
    assert expected_file.exists()

    with open(expected_file, encoding="utf-8") as f:
        payload = json.load(f)
        assert payload["data"] == {
            "data": "result-7",
            "timestamp": ANY
        }
