# test_cache_manager.py

import pytest
import json
from core.cache.cache_manager import CacheManager
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_cache_manager_basic_set_get(cache_manager):
    key = "test_key"
    data = {"foo": 123}
    cache_manager.set(key, data)

    loaded = cache_manager.get(key)
    assert loaded["foo"] == 123

@pytest.mark.asyncio
async def test_cache_manager_delete(cache_manager):
    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    cache_manager.set(key, value)
    assert cache_manager.get(key) == value

    # 2. delete
    cache_manager.delete(key)
    assert cache_manager.get(key) is None

@pytest.mark.asyncio
async def test_cache_manager_delete_failure(cache_manager):
    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    cache_manager.set(key, value)
    assert cache_manager.get(key) == value

    key2 = "test_key2"

    # 2. delete
    cache_manager.delete(key2)
    assert cache_manager.get(key) is not None


def test_cache_manager_set_creates_file(tmp_path):
    # ✅ base_dir을 tmp_path로 설정
    config = {
        "cache": {
            "base_dir": str(tmp_path),
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }
    cache_manager = CacheManager(config=config)

    test_key = "file_cache_test"
    test_value = {"x": 123}

    # Act
    cache_manager.set(test_key, test_value, save_to_file=True)

    # Assert
    expected_file = tmp_path / f"{test_key}.json"
    assert expected_file.exists()

def test_cache_manager_file_cache_reuse(cache_manager, tmp_path):
    key = "file_reuse_test"
    value = {"y": 999}
    cache_manager.set(key, value, save_to_file=True)

    # 메모리 클리어 (의도적으로)
    cache_manager.memory_cache.clear()

    # 캐시 재조회 시 파일에서 읽히는지 확인
    loaded = cache_manager.get(key)
    assert loaded == value
