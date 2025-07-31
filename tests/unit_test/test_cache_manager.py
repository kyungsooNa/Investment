# test_get_cache_manager().py

import pytest
import json
from core.cache.cache_manager import get_cache_manager
from unittest.mock import MagicMock
from core.cache.cache_wrapper import cache_wrap_client, CACHED_METHODS
from core.cache.cache_manager import CacheManager


def test_cache_manager_singleton_creation():
    # ⚠️ 기존 인스턴스 제거 (리셋)
    CacheManager._instance = None

    # 최초 생성 시 분기 커버
    instance1 = CacheManager()
    assert isinstance(instance1, CacheManager)
    assert CacheManager._instance is instance1

def test_cache_manager_singleton_reuse():
    # 이미 생성된 인스턴스를 사용
    instance1 = CacheManager()
    instance2 = CacheManager()

    # 두 인스턴스가 동일한지 확인 (싱글턴)
    assert instance1 is instance2


@pytest.mark.asyncio
async def test_cache_manager_basic_set_get():
    get_cache_manager().clear()
    key = "test_key"
    value = {"data": 123}

    get_cache_manager().set(key, value)
    result = get_cache_manager().get(key)

    assert result == value

@pytest.mark.asyncio
async def test_cache_manager_delete():
    get_cache_manager().clear()

    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    get_cache_manager().set(key, value)
    assert get_cache_manager().get(key) == value

    # 2. delete
    get_cache_manager().delete(key)
    assert get_cache_manager().get(key) is None

@pytest.mark.asyncio
async def test_cache_manager_delete_failure():
    get_cache_manager().clear()

    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    get_cache_manager().set(key, value)
    assert get_cache_manager().get(key) == value

    key2 = "test_key2"

    # 2. delete
    get_cache_manager().delete(key2)
    assert get_cache_manager().get(key) is not None


def test_cache_manager_set_creates_file(tmp_path):
    CacheManager._instance = None
    cache_manager = CacheManager()

    # base_dir을 강제로 임시 디렉토리로 설정
    get_cache_manager()._base_dir = str(tmp_path)
    test_key = "file_cache_test"
    test_value = {"x": 123}

    # Act
    get_cache_manager().set(test_key, test_value, save_to_file=True)

    # Assert
    expected_file = tmp_path / f"{test_key}.json"
    assert expected_file.exists()

    with open(expected_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
        assert "data" in loaded
        assert loaded["data"]["x"] == 123

def test_cache_manager_file_cache_reuse(tmp_path):
    CacheManager._instance = None
    manager = CacheManager()
    manager._base_dir = str(tmp_path)

    key = "file_reuse_test"
    value = {"y": 999}
    manager.set(key, value, save_to_file=True)

    # 메모리 클리어 (의도적으로)
    manager._cache.clear()

    # 캐시 재조회 시 파일에서 읽히는지 확인
    loaded = manager.get(key)
    assert loaded == value
