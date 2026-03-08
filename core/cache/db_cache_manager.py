# core/cache/db_cache_manager.py

import os
import json
import time
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional, Any
from dataclasses import fields, is_dataclass
from pydantic import BaseModel
from core.cache.cache_config import load_cache_config
from core.cache.file_cache_manager import load_deserializable_classes

class DBCacheManager:
    def __init__(self, config: Optional[dict] = None):
        if config is None:
            config = load_cache_config()
        self._base_dir = config["cache"]["base_dir"]
        self._db_path = os.path.join(self._base_dir, "cache.db")
        self._logger = None
        self._deserializable_classes = load_deserializable_classes(config["cache"].get("deserializable_classes", []))
        
        os.makedirs(self._base_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_updated_at ON cache(updated_at)")
        except Exception as e:
            if self._logger:
                self._logger.error(f"❌ DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def __del__(self):
        if hasattr(self, "_conn") and self._conn:
            self._conn.close()

    def set_logger(self, logger):
        self._logger = logger

    def _serialize(self, value: Any) -> Any:
        if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
            return value.to_dict()
        elif isinstance(value, BaseModel):
            return value.model_dump()
        elif isinstance(value, (list, tuple)):
            return [self._serialize(item) for item in value]
        elif isinstance(value, dict):
            return {k: self._serialize(v) for k, v in value.items()}
        return value

    def _deserialize(self, raw_data: Any) -> Any:
        if isinstance(raw_data, dict):
            for cls in self._deserializable_classes:
                try:
                    if issubclass(cls, BaseModel):
                        cls_fields = set(cls.model_fields.keys())
                        if cls_fields.issubset(raw_data.keys()):
                            if cls.__name__ == "ResCommonResponse" and "data" in raw_data:
                                raw_data["data"] = self._deserialize(raw_data["data"])
                            return cls.model_validate(raw_data)
                    elif is_dataclass(cls):
                        cls_fields = {f.name for f in fields(cls)}
                        if cls_fields.issubset(raw_data.keys()):
                            if cls.__name__ == "ResCommonResponse" and "data" in raw_data:
                                raw_data["data"] = self._deserialize(raw_data["data"])
                            return cls.from_dict(raw_data)
                except Exception as e:
                    ...
            return {k: self._deserialize(v) for k, v in raw_data.items()}
        elif isinstance(raw_data, (list, tuple)):
            return [self._deserialize(item) for item in raw_data]
        return raw_data

    def set(self, key: str, value: Any, save_to_file: bool = False):
        if save_to_file:
            try:
                serialized_data = self._serialize(value)
                json_str = json.dumps(serialized_data, ensure_ascii=False)
                now = time.time()
                
                with self._get_connection() as conn:
                    conn.execute("INSERT OR REPLACE INTO cache (key, value, updated_at) VALUES (?, ?, ?)", (key, json_str, now))
                
                if self._logger:
                    self._logger.debug(f"💾 DB cache 저장: {key}")
            except Exception as e:
                if self._logger:
                    self._logger.error(f"❌ DB cache 저장 실패: {e}")

    def delete(self, key: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            if self._logger:
                self._logger.debug(f"🗑️ DB cache 삭제됨: {key}")
        except Exception as e:
            if self._logger:
                self._logger.error(f"❌ DB cache 삭제 실패: {e}")

    def clear(self):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM cache")
            if self._logger:
                self._logger.debug("🗑️ 전체 DB cache 삭제됨")
        except Exception as e:
            if self._logger:
                self._logger.error(f"❌ 전체 DB 캐시 삭제 실패: {e}")

    def cleanup_old_files(self, days: int = 7, max_size_mb: int = 0):
        cutoff = time.time() - (days * 86400)
        ohlcv_cutoff = time.time() - (365 * 86400)  # OHLCV 데이터는 1년 보관

        try:
            with self._get_connection() as conn:
                # 일반 데이터 삭제 (OHLCV 및 지표 제외)
                conn.execute("DELETE FROM cache WHERE updated_at < ? AND key NOT LIKE 'ohlcv_past_%' AND key NOT LIKE 'indicators_chart_%'", (cutoff,))
                # OHLCV 및 지표 데이터 삭제 (1년 경과)
                conn.execute("DELETE FROM cache WHERE updated_at < ? AND (key LIKE 'ohlcv_past_%' OR key LIKE 'indicators_chart_%')", (ohlcv_cutoff,))

                # 용량 제한 적용 (데이터 크기 기준)
                if max_size_mb > 0:
                    cursor = conn.execute("SELECT SUM(LENGTH(value)) FROM cache")
                    total_size = cursor.fetchone()[0] or 0
                    limit_size = max_size_mb * 1024 * 1024

                    if total_size > limit_size:
                        bytes_to_remove = total_size - limit_size
                        # 오래된 순으로 조회하여 삭제할 키 수집
                        rows = conn.execute("SELECT key, LENGTH(value) FROM cache ORDER BY updated_at ASC").fetchall()
                        
                        keys_to_delete = []
                        removed_amount = 0
                        for key, size in rows:
                            keys_to_delete.append(key)
                            removed_amount += (size or 0)
                            if removed_amount >= bytes_to_remove:
                                break
                        
                        if keys_to_delete:
                            # SQLite 변수 제한 고려하여 배치 삭제 (900개씩)
                            for i in range(0, len(keys_to_delete), 900):
                                batch = keys_to_delete[i:i+900]
                                placeholders = ','.join('?' for _ in batch)
                                conn.execute(f"DELETE FROM cache WHERE key IN ({placeholders})", batch)
                            
                            if self._logger:
                                self._logger.debug(f"🗑️ DB cache 용량 초과로 {len(keys_to_delete)}개 항목 삭제됨")

            if self._logger:
                self._logger.debug(f"🗑️ 오래된 DB cache 정리 완료 (기준: {days}일 전)")
        except Exception as e:
            if self._logger:
                self._logger.error(f"❌ DB 캐시 정리 실패: {e}")

    def get_raw(self, key: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT value FROM cache WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    wrapper = json.loads(row[0])
                    data = wrapper["data"]
                    wrapper['data'] = self._deserialize(data)
                    return wrapper
        except Exception as e:
            if self._logger:
                self._logger.error(f"[DBCache] Load Error: {e}")
        return None

    def exists(self, key: str) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT 1 FROM cache WHERE key = ?", (key,))
                return cursor.fetchone() is not None
        except:
            return False