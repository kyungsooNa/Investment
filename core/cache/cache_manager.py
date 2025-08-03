# core/cache_manager.py

from typing import Any, Optional
from datetime import datetime
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
        self.memory_cache.set_logger(logger)
        self.file_cache.set_logger(logger)

    def get(self, key: str) -> Optional[Any]:
        """단순 value 반환"""
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

    def get_raw(self, key: str) -> Optional[dict]:
        """메모리 또는 파일 캐시에서 (timestamp + data) 반환"""
        # 메모리 캐시에 timestamp 포함된 wrapper가 저장되어 있다고 가정
        raw = None
        raw_memory = self.memory_cache.get(key)

        if raw_memory:
            raw = raw_memory
            if self._logger:
                self._logger.debug(f"🧠 Memory Cache HIT: {key}")
        else:
            if self._logger:
                self._logger.debug(f"🧠 Memory Cache MISS: {key}")
            raw_file = self.file_cache.get_raw(key)

            if raw_file:
                self.memory_cache.set(key, raw_file)
                raw = raw_file
                if self._logger:
                    self._logger.debug(f"📂 File Cache HIT: {key}")
            else:
                if self._logger:
                    self._logger.debug(f"🚫 File Cache MISS: {key}")

        if not isinstance(raw, dict) or "timestamp" not in raw or "data" not in raw:
            if self._logger:
                self._logger.warning(f"[CacheManager] ❌ 잘못된 캐시 구조 감지: {key} / 내용: {raw}")
            return None

        try:
            # timestamp가 ISO 형식인지 유효성 검사
            datetime.fromisoformat(raw["timestamp"])
        except Exception as e:
            if self._logger:
                self._logger.warning(f"[CacheManager] ❌ 잘못된 timestamp 형식: {raw['timestamp']}")
            return None

        return raw

    def set(self, key: str, value: Any, save_to_file: bool = False):
        self.memory_cache.set(key, value)
        if save_to_file:
            self.file_cache.set(key, value, save_to_file)

    def delete(self, key: str):
        self.memory_cache.delete(key)
        self.file_cache.delete(key)

    def clear(self):
        self.memory_cache.clear()
        self.file_cache.clear()
