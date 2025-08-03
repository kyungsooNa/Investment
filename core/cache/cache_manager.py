# core/cache_manager.py

from typing import Any, Optional
from core.cache.cache_config import load_cache_config
from core.cache.memory_cache_manager import MemoryCacheManager
from core.cache.file_cache_manager import FileCacheManager


class CacheManager:
    def __init__(self, config: Optional[dict] = None):
        if config is None:
            config = load_cache_config()
        self.memory_cache = MemoryCacheManager()
        self.file_cache = FileCacheManager(config)
        self._logger = None

    def set_logger(self, logger):
        self._logger = logger

    def get(self, key: str) -> Optional[Any]:
        val = self.memory_cache.get(key)
        if val is not None:
            if self._logger:
                self._logger.debug(f"✅ Memory Cache HIT: {key}")
            return val

        if self._logger:
            self._logger.debug(f"🚫 Memory Cache MISS: {key}")

        val = self.file_cache.get(key)
        if val is not None:
            if self._logger:
                self._logger.debug(f"📂 File Cache HIT: {key}")
            self.memory_cache.set(key, val)
        else:
            if self._logger:
                self._logger.debug(f"🚫 File Cache MISS: {key}")
        return val

    def set(self, key: str, value: Any, save_to_file: bool = False):
        self.memory_cache.set(key, value)
        if save_to_file:
            self.file_cache.set(key, value, save_to_file)

    def delete(self, key: str):
        self.memory_cache.delete(key)
        self.file_cache.delete(key)

    def clear(self):
        self.memory_cache.clear()
        # Optional: self.file_cache.clear()
