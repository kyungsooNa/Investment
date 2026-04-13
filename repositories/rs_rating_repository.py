# repositories/rs_rating_repository.py
"""
RS Rating (IBD/오닐 방식 1~99 백분위) 일자별 데이터를 SQLite에 저장/조회하는 Repository.
- 테이블: rs_ratings (trade_date, code, rs_rating, weighted_rs)
- 배치 upsert / 날짜별 전체 조회 / 종목별 이력 조회 지원
"""
import os
import sqlite3
import asyncio
import aiosqlite
import logging
from typing import Optional, List, Dict, Any

from common.types import ResRSRating


class RSRatingRepository:
    """RS Rating 전담 저장소 (SQLite)."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._db_path = db_path or os.path.join("data", "stocks.db")
        self._write_lock: Optional[asyncio.Lock] = None
        self._write_conn: Optional[aiosqlite.Connection] = None
        self._read_conn: Optional[aiosqlite.Connection] = None
        self._read_conn_lock: Optional[asyncio.Lock] = None

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db_sync()

    def _init_db_sync(self):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rs_ratings (
                        trade_date TEXT NOT NULL,
                        code TEXT NOT NULL,
                        rs_rating INTEGER NOT NULL,
                        weighted_rs REAL NOT NULL,
                        PRIMARY KEY (trade_date, code)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rs_ratings_date "
                    "ON rs_ratings(trade_date)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rs_ratings_code "
                    "ON rs_ratings(code)"
                )
                conn.commit()
        except Exception as e:
            self._logger.error(f"RSRatingRepository DB 초기화 실패: {e}")

    async def _get_write_conn(self) -> aiosqlite.Connection:
        if self._write_conn is None:
            self._write_conn = await aiosqlite.connect(self._db_path)
            await self._write_conn.execute("PRAGMA journal_mode=WAL")
            await self._write_conn.execute("PRAGMA synchronous=NORMAL")
        return self._write_conn

    async def _get_read_conn(self) -> aiosqlite.Connection:
        if self._read_conn is None:
            self._read_conn = await aiosqlite.connect(self._db_path)
            await self._read_conn.execute("PRAGMA journal_mode=WAL")
        return self._read_conn

    async def _ensure_locks(self):
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        if self._read_conn_lock is None:
            self._read_conn_lock = asyncio.Lock()

    async def upsert_batch(self, records: List[Dict[str, Any]]) -> int:
        """배치 upsert: [{"trade_date", "code", "rs_rating", "weighted_rs"}] 리스트.
        성공적으로 저장된 레코드 수를 반환.
        """
        if not records:
            return 0
        await self._ensure_locks()
        async with self._write_lock:
            try:
                conn = await self._get_write_conn()
                await conn.executemany(
                    """
                    INSERT INTO rs_ratings (trade_date, code, rs_rating, weighted_rs)
                    VALUES (:trade_date, :code, :rs_rating, :weighted_rs)
                    ON CONFLICT(trade_date, code) DO UPDATE SET
                        rs_rating  = excluded.rs_rating,
                        weighted_rs = excluded.weighted_rs
                    """,
                    records,
                )
                await conn.commit()
                return len(records)
            except Exception as e:
                self._logger.error(f"RSRatingRepository.upsert_batch 실패: {e}")
                return 0

    async def get_by_date(self, trade_date: str) -> Dict[str, int]:
        """{code: rs_rating} 딕셔너리 반환. 해당 날짜 데이터가 없으면 빈 딕셔너리."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    "SELECT code, rs_rating FROM rs_ratings WHERE trade_date = ?",
                    (trade_date,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
            except Exception as e:
                self._logger.error(f"RSRatingRepository.get_by_date({trade_date}) 실패: {e}")
                return {}

    async def get_by_code(self, code: str, limit: int = 60) -> List[ResRSRating]:
        """종목별 RS Rating 이력 조회 (최신순, limit 건)."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    """
                    SELECT trade_date, code, rs_rating, weighted_rs
                    FROM rs_ratings
                    WHERE code = ?
                    ORDER BY trade_date DESC
                    LIMIT ?
                    """,
                    (code, limit),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [
                    ResRSRating(
                        trade_date=row[0],
                        code=row[1],
                        rs_rating=row[2],
                        weighted_rs=row[3],
                    )
                    for row in rows
                ]
            except Exception as e:
                self._logger.error(f"RSRatingRepository.get_by_code({code}) 실패: {e}")
                return []

    async def get_single(self, code: str, trade_date: str) -> Optional[ResRSRating]:
        """단일 종목+날짜 조회. 없으면 None."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    """
                    SELECT trade_date, code, rs_rating, weighted_rs
                    FROM rs_ratings
                    WHERE code = ? AND trade_date = ?
                    """,
                    (code, trade_date),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    return None
                return ResRSRating(
                    trade_date=row[0],
                    code=row[1],
                    rs_rating=row[2],
                    weighted_rs=row[3],
                )
            except Exception as e:
                self._logger.error(f"RSRatingRepository.get_single({code},{trade_date}) 실패: {e}")
                return None

    async def get_latest_date(self) -> Optional[str]:
        """가장 최근에 계산된 trade_date 반환. 데이터가 없으면 None."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    "SELECT MAX(trade_date) FROM rs_ratings"
                ) as cursor:
                    row = await cursor.fetchone()
                return row[0] if row and row[0] else None
            except Exception as e:
                self._logger.error(f"RSRatingRepository.get_latest_date 실패: {e}")
                return None

    async def close(self):
        """비동기 연결 종료."""
        if self._write_conn:
            await self._write_conn.close()
            self._write_conn = None
        if self._read_conn:
            await self._read_conn.close()
            self._read_conn = None
