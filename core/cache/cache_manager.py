# core/cache_manager.py

from typing import Any, Optional, Tuple
from datetime import datetime
from core.cache.cache_config import load_cache_config
from core.cache.memory_cache_manager import MemoryCacheManager
from core.cache.file_cache_manager import FileCacheManager


class CacheManager:
    def __init__(self, config: Optional[dict] = None):
        if config is None:
            config = load_cache_config()
        cache_cfg = config.get("cache", {})

        self.memory_cache = MemoryCacheManager() if cache_cfg.get("memory_cache_enabled", True) else None
        self.file_cache = FileCacheManager(config) if cache_cfg.get("file_cache_enabled", True) else None
        self._logger = None

    def set_logger(self, logger):
        self._logger = logger
        if self.memory_cache:
            self.memory_cache.set_logger(logger)
        if self.file_cache:
            self.file_cache.set_logger(logger)

    def get_raw(self, key: str) -> Optional[Tuple[dict, str]] | None:
        """메모리 또는 파일 캐시에서 (timestamp + data) 반환"""
        # 메모리 캐시에 timestamp 포함된 wrapper가 저장되어 있다고 가정
        raw = None
        cache_type = ""

        # 1) 메모리 캐시 조회 (켜져 있을 때만)
        if self.memory_cache:
            raw_memory = self.memory_cache.get(key)
            if raw_memory:
                raw = raw_memory
                cache_type = "memory"
                if self._logger:
                    self._logger.debug(f"🧠 Memory Cache HIT: {key}")
            else:
                if self._logger:
                    self._logger.debug(f"🧠 Memory Cache MISS: {key}")

        # 2) 파일 캐시 조회 (켜져 있고 아직 못 찾았을 때만)
        if raw is None and self.file_cache:
            raw_file = self.file_cache.get_raw(key)
            if raw_file:
                # ✅ 메모리 warm-up은 메모리 캐시가 켜져 있을 때만
                if self.memory_cache:
                    self.memory_cache.set(key, raw_file)
                raw = raw_file
                cache_type = "file"
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

        return raw, cache_type

    def set(self, key: str, value: Any, save_to_file: bool = False):
        if self.memory_cache:
            self.memory_cache.set(key, value)
        if save_to_file and self.file_cache:
            self.file_cache.set(key, value, save_to_file)

    def delete(self, key: str):
        if self.memory_cache:
            self.memory_cache.delete(key)
        if self.file_cache:
            self.file_cache.delete(key)

    def clear(self):
        if self.memory_cache:
            self.memory_cache.clear()
        if self.file_cache:
            self.file_cache.clear()
