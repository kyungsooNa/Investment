# core/cache/memory_cache_manager.py

from typing import Any, Dict, Optional

class MemoryCacheManager:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._logger = None

    def set_logger(self, logger):
        self._logger = logger

    def get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)  # ✅ 안전하게 조회

    def set(self, key: str, value: any):
        self._cache[key] = value

    def has(self, key: str) -> bool:  # ✅ 추가
        return key in self._cache

    def delete(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()
