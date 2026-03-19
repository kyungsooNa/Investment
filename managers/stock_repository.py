# managers/stock_repository.py
"""
개별 종목의 상세 데이터(OHLCV 등)와 인메모리 캐시를 관리하는 Repository.
차트 조회 및 개별 종목 지표 계산을 위한 초고속 데이터 제공을 목적으로 한다.
"""
import os
import sqlite3
import time
import logging
import threading
import collections
from contextlib import contextmanager
from typing import Optional, List, Dict


class _LRUCache:
    """내장 OrderedDict를 활용한 인메모리 LRU(Least Recently Used) 캐시"""
    def __init__(self, capacity: int = 500):
        self.cache = collections.OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)


class StockRepository:
    """개별 종목 데이터(OHLCV, 캐시) 전담 저장소."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        # MarketData와 DB 파일을 물리적으로 분리하여 Lock 경합 방지
        self._db_path = db_path or os.path.join("data", "stocks.db")
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        # 최대 500개 종목의 통합 데이터(현재가 + OHLCV)를 들고 있는 인메모리 캐시
        self._stocks_cache = _LRUCache(capacity=500)

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv (
                        code TEXT NOT NULL,
                        date TEXT NOT NULL,
                        open INTEGER,
                        high INTEGER,
                        low INTEGER,
                        close INTEGER,
                        volume INTEGER,
                        PRIMARY KEY (code, date)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date)")
        except Exception as e:
            self._logger.error(f"StockRepository DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def upsert_ohlcv(self, records: List[Dict]):
        """여러 종목의 일봉(OHLCV) 데이터를 일괄 upsert (INSERT OR REPLACE)."""
        if not records:
            return

        try:
            with self._get_connection() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO ohlcv (
                        code, date, open, high, low, close, volume
                    ) VALUES (
                        :code, :date, :open, :high, :low, :close, :volume
                    )
                    """,
                    records,
                )
        except Exception as e:
            self._logger.error(f"StockRepository OHLCV upsert 실패: {e}")

    def close(self):
        """DB 연결을 닫는다."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()