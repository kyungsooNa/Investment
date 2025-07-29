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
