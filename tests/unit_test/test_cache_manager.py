# test_cache_manager.py

import pytest
from core.cache_manager import cache_manager, CacheManager


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
    cache_manager.clear()
    key = "test_key"
    value = {"data": 123}

    cache_manager.set(key, value)
    result = cache_manager.get(key)

    assert result == value

@pytest.mark.asyncio
async def test_cache_manager_delete():
    cache_manager.clear()

    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    cache_manager.set(key, value)
    assert cache_manager.get(key) == value

    # 2. delete
    cache_manager.delete(key)
    assert cache_manager.get(key) is None

@pytest.mark.asyncio
async def test_cache_manager_delete_failure():
    cache_manager.clear()

    key = "test_key"
    value = {"foo": "bar"}

    # 1. set
    cache_manager.set(key, value)
    assert cache_manager.get(key) == value

    key2 = "test_key2"

    # 2. delete
    cache_manager.delete(key2)
    assert cache_manager.get(key) is not None
