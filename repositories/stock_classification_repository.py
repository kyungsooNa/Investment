# repositories/stock_classification_repository.py
"""
종목 테마/업종 분류를 다중 소스(NAVER/KIWOOM/WICS)로 저장·조회하는 Repository.

- 테이블: stock_classifications (source, category_type, group_name, normalized_name, code, name, collected_at)
- 테이블: theme_aliases (source, raw_name, normalized_name)
- `stocks` 처럼 주기적으로 replace 되는 휘발성 DB와 분리하기 위해 전용 DB 파일을 사용한다.
- 조회는 normalized_name 기준으로 소스를 union 하고, 종목별 출처(provenance)를 보존한다.
"""
import os
import sqlite3
import asyncio
import aiosqlite
import logging
from typing import Optional, List, Dict, Any


class StockClassificationRepository:
    """종목 분류(테마/업종) 전담 저장소 (SQLite, 다중 소스 union)."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._db_path = db_path or os.path.join("data", "stock_classifications.db")
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
                    CREATE TABLE IF NOT EXISTS stock_classifications (
                        source TEXT NOT NULL,
                        category_type TEXT NOT NULL,
                        group_name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT,
                        collected_at TEXT,
                        PRIMARY KEY (source, category_type, group_name, code)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS theme_aliases (
                        source TEXT NOT NULL,
                        raw_name TEXT NOT NULL,
                        normalized_name TEXT NOT NULL,
                        PRIMARY KEY (source, raw_name)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_classifications_norm "
                    "ON stock_classifications(category_type, normalized_name)"
                )
                conn.commit()
        except Exception as e:
            self._logger.error(f"StockClassificationRepository DB 초기화 실패: {e}")

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

    async def upsert_classifications(self, records: List[Dict[str, Any]]) -> int:
        """배치 upsert.

        records: [{"source", "category_type", "group_name", "normalized_name",
                   "code", "name", "collected_at"}] 리스트.
        성공적으로 저장 시도한 레코드 수를 반환.
        """
        if not records:
            return 0
        await self._ensure_locks()
        async with self._write_lock:
            try:
                conn = await self._get_write_conn()
                await conn.executemany(
                    """
                    INSERT INTO stock_classifications
                        (source, category_type, group_name, normalized_name, code, name, collected_at)
                    VALUES
                        (:source, :category_type, :group_name, :normalized_name, :code, :name, :collected_at)
                    ON CONFLICT(source, category_type, group_name, code) DO UPDATE SET
                        normalized_name = excluded.normalized_name,
                        name            = excluded.name,
                        collected_at    = excluded.collected_at
                    """,
                    records,
                )
                await conn.commit()
                return len(records)
            except Exception as e:
                self._logger.error(f"StockClassificationRepository.upsert_classifications 실패: {e}")
                return 0

    async def upsert_aliases(self, records: List[Dict[str, str]]) -> int:
        """theme_aliases 배치 upsert. records: [{"source","raw_name","normalized_name"}]."""
        if not records:
            return 0
        await self._ensure_locks()
        async with self._write_lock:
            try:
                conn = await self._get_write_conn()
                await conn.executemany(
                    """
                    INSERT INTO theme_aliases (source, raw_name, normalized_name)
                    VALUES (:source, :raw_name, :normalized_name)
                    ON CONFLICT(source, raw_name) DO UPDATE SET
                        normalized_name = excluded.normalized_name
                    """,
                    records,
                )
                await conn.commit()
                return len(records)
            except Exception as e:
                self._logger.error(f"StockClassificationRepository.upsert_aliases 실패: {e}")
                return 0

    async def get_alias_map(self, source: str) -> Dict[str, str]:
        """특정 소스의 {raw_name: normalized_name} 매핑 반환. 없으면 빈 딕셔너리."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    "SELECT raw_name, normalized_name FROM theme_aliases WHERE source = ?",
                    (source,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
            except Exception as e:
                self._logger.error(f"StockClassificationRepository.get_alias_map({source}) 실패: {e}")
                return {}

    async def get_groups(
        self,
        category_types: tuple = ("theme",),
        sources: Optional[tuple] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """normalized_name 기준으로 분류 그룹을 union 하여 반환.

        Returns:
            {
                normalized_name: {
                    "sources": ["NAVER", ...],   # 그룹 전체 소스(정렬)
                    "members": [{"code", "name", "sources": [...]}, ...]
                }, ...
            }
        데이터가 없으면 빈 딕셔너리.
        """
        if not category_types:
            return {}
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                params: list = list(category_types)
                cat_ph = ",".join("?" for _ in category_types)
                sql = (
                    "SELECT normalized_name, code, name, source "
                    "FROM stock_classifications "
                    f"WHERE category_type IN ({cat_ph})"
                )
                if sources:
                    src_ph = ",".join("?" for _ in sources)
                    sql += f" AND source IN ({src_ph})"
                    params.extend(sources)
                async with conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
            except Exception as e:
                self._logger.error(f"StockClassificationRepository.get_groups 실패: {e}")
                return {}

        # normalized_name → {code → {name, sources(set)}}, group sources(set)
        groups: Dict[str, Dict[str, Any]] = {}
        for normalized_name, code, name, source in rows:
            g = groups.setdefault(normalized_name, {"_sources": set(), "_members": {}})
            g["_sources"].add(source)
            m = g["_members"].setdefault(code, {"code": code, "name": name, "_sources": set()})
            m["_sources"].add(source)
            if name and not m["name"]:
                m["name"] = name

        result: Dict[str, Dict[str, Any]] = {}
        for normalized_name, g in groups.items():
            members = [
                {"code": m["code"], "name": m["name"], "sources": sorted(m["_sources"])}
                for m in g["_members"].values()
            ]
            result[normalized_name] = {
                "sources": sorted(g["_sources"]),
                "members": members,
            }
        return result

    async def get_latest_collected_at(self) -> Optional[str]:
        """가장 최근 collected_at 값 반환. 데이터가 없으면 None."""
        await self._ensure_locks()
        async with self._read_conn_lock:
            try:
                conn = await self._get_read_conn()
                async with conn.execute(
                    "SELECT MAX(collected_at) FROM stock_classifications"
                ) as cursor:
                    row = await cursor.fetchone()
                return row[0] if row and row[0] else None
            except Exception as e:
                self._logger.error(f"StockClassificationRepository.get_latest_collected_at 실패: {e}")
                return None

    async def close(self):
        """비동기 연결 종료."""
        if self._write_conn:
            await self._write_conn.close()
            self._write_conn = None
        if self._read_conn:
            await self._read_conn.close()
            self._read_conn = None
