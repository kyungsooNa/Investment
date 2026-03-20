# managers/market_data_repository.py
"""
전체 종목 마켓 데이터를 SQLite에 저장/조회하는 Repository.
장 마감 후 수집한 현재가 및 관련 데이터를 날짜별로 관리한다.
"""
import os
import sqlite3
import time
import logging
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict


class MarketDataRepository:
    """전체 종목 마켓 데이터를 SQLite에 저장/조회하는 Repository."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._db_path = db_path or os.path.join("data", "market_data.db")
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """daily_prices 테이블 생성 (WAL 모드)."""
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS daily_prices (
                        code TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        name TEXT,
                        current_price INTEGER,
                        open_price INTEGER,
                        high_price INTEGER,
                        low_price INTEGER,
                        prev_close INTEGER,
                        change_price INTEGER,
                        change_sign TEXT,
                        change_rate TEXT,
                        volume INTEGER,
                        trading_value INTEGER,
                        market_cap INTEGER,
                        per REAL,
                        pbr REAL,
                        eps REAL,
                        w52_high INTEGER,
                        w52_low INTEGER,
                        market TEXT,
                        collected_at REAL,
                        PRIMARY KEY (code, trade_date)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_daily_prices_trade_date "
                    "ON daily_prices(trade_date)"
                )
        except Exception as e:
            self._logger.error(f"MarketDataRepository DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self):
        """DB 연결을 닫는다."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()

    def upsert_prices(self, trade_date: str, records: List[Dict]):
        """여러 종목의 현재가를 일괄 upsert (INSERT OR REPLACE)."""
        if not records:
            return

        now = time.time()
        try:
            with self._get_connection() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO daily_prices (
                        code, trade_date, name,
                        current_price, open_price, high_price, low_price, prev_close,
                        change_price, change_sign, change_rate,
                        volume, trading_value, market_cap,
                        per, pbr, eps,
                        w52_high, w52_low,
                        market, collected_at
                    ) VALUES (
                        :code, :trade_date, :name,
                        :current_price, :open_price, :high_price, :low_price, :prev_close,
                        :change_price, :change_sign, :change_rate,
                        :volume, :trading_value, :market_cap,
                        :per, :pbr, :eps,
                        :w52_high, :w52_low,
                        :market, :collected_at
                    )
                    """,
                    [{**r, "trade_date": trade_date, "collected_at": now} for r in records],
                )
            self._logger.debug(
                f"MarketDataRepository: {len(records)}건 upsert 완료 (date={trade_date})"
            )
        except Exception as e:
            self._logger.error(f"MarketDataRepository upsert 실패: {e}")

    def get_prices_by_date(self, trade_date: str) -> List[Dict]:
        """특정 날짜의 전체 종목 현재가 조회."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM daily_prices WHERE trade_date = ? ORDER BY code",
                    (trade_date,),
                )
                rows = cursor.fetchall()
                conn.row_factory = None
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"MarketDataRepository 날짜별 조회 실패: {e}")
            return []

    def get_price_history(self, code: str, days: int = 30) -> List[Dict]:
        """특정 종목의 최근 N일간 가격 이력 조회."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM daily_prices WHERE code = ? "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (code, days),
                )
                rows = cursor.fetchall()
                conn.row_factory = None
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"MarketDataRepository 이력 조회 실패: {e}")
            return []

    def get_latest_trade_date(self) -> Optional[str]:
        """DB에 저장된 가장 최근 거래일 반환."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT MAX(trade_date) FROM daily_prices"
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] else None
        except Exception as e:
            self._logger.error(f"MarketDataRepository 최근 거래일 조회 실패: {e}")
            return None

    def get_count_by_date(self, trade_date: str) -> int:
        """특정 날짜의 저장된 종목 수 반환."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM daily_prices WHERE trade_date = ?",
                    (trade_date,),
                )
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            self._logger.error(f"MarketDataRepository 카운트 조회 실패: {e}")
            return 0

    def cleanup_old_data(self, keep_days: int = 365):
        """오래된 데이터 정리."""
        from datetime import datetime, timedelta

        cutoff_date = (datetime.now() - timedelta(days=keep_days)).strftime("%Y%m%d")
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM daily_prices WHERE trade_date < ?", (cutoff_date,)
                )
                deleted = cursor.rowcount
                if deleted > 0:
                    self._logger.info(
                        f"MarketDataRepository: {deleted}건 오래된 데이터 삭제 (기준: {cutoff_date})"
                    )
        except Exception as e:
            self._logger.error(f"MarketDataRepository 데이터 정리 실패: {e}")
