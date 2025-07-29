# test_cache_manager.py

import pytest
from core.cache_manager import cache_manager

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
