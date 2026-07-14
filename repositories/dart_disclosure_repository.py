"""OpenDART 공시 감지·발송 상태 SQLite 저장소."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

import aiosqlite

from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance


_DDL_DISCLOSURES = """
CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no TEXT PRIMARY KEY,
    corp_code TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    corp_name TEXT NOT NULL,
    report_name TEXT NOT NULL,
    filer_name TEXT NOT NULL,
    receipt_date TEXT NOT NULL,
    remarks TEXT NOT NULL,
    importance_score INTEGER NOT NULL,
    importance_level TEXT NOT NULL,
    importance_reasons TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    alert_suppressed INTEGER NOT NULL DEFAULT 0,
    immediate_sent_at TEXT,
    digest_sent_at TEXT,
    send_retry_count INTEGER NOT NULL DEFAULT 0
)
"""

_DDL_STATE = """
CREATE TABLE IF NOT EXISTS monitor_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class StoredDisclosure:
    disclosure: DartDisclosure
    importance: DisclosureImportance


class DartDisclosureRepository:
    DB_PATH = Path("data/dart_disclosures.db")

    def __init__(self, db_path: Union[str, Path, None] = None) -> None:
        self._db_path = Path(db_path or self.DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _setup(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(_DDL_DISCLOSURES)
        await conn.execute(_DDL_STATE)
        await conn.commit()

    async def save_detected(
        self,
        disclosure: DartDisclosure,
        importance: DisclosureImportance,
        *,
        suppress_immediate: bool = False,
    ) -> bool:
        detected_at = datetime.now().isoformat()
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO disclosures(
                    rcept_no, corp_code, stock_code, corp_name, report_name,
                    filer_name, receipt_date, remarks, importance_score,
                    importance_level, importance_reasons, detected_at, alert_suppressed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    disclosure.receipt_no,
                    disclosure.corp_code,
                    disclosure.stock_code,
                    disclosure.corp_name,
                    disclosure.report_name,
                    disclosure.filer_name,
                    disclosure.receipt_date,
                    disclosure.remarks,
                    importance.score,
                    importance.level,
                    json.dumps(importance.reasons, ensure_ascii=False),
                    detected_at,
                    int(suppress_immediate),
                ),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def has_receipt(self, receipt_no: str) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT 1 FROM disclosures WHERE rcept_no = ? LIMIT 1", (receipt_no,)
            ) as cur:
                return await cur.fetchone() is not None

    async def get_known_receipt_nos(self, receipt_nos: Iterable[str]) -> set[str]:
        receipt_nos = list(dict.fromkeys(str(value) for value in receipt_nos if value))
        if not receipt_nos:
            return set()
        placeholders = ",".join("?" for _ in receipt_nos)
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                f"SELECT rcept_no FROM disclosures WHERE rcept_no IN ({placeholders})",
                tuple(receipt_nos),
            ) as cur:
                rows = await cur.fetchall()
        return {str(row[0]) for row in rows}

    async def get_pending_immediate(self, threshold: int) -> list[StoredDisclosure]:
        return await self._query_stored(
            """
            SELECT * FROM disclosures
            WHERE importance_score >= ? AND immediate_sent_at IS NULL AND alert_suppressed = 0
            ORDER BY detected_at ASC, rcept_no ASC
            """,
            (int(threshold),),
        )

    async def mark_immediate_sent(self, receipt_no: str, sent_at: datetime) -> None:
        await self._update_timestamp("immediate_sent_at", [receipt_no], sent_at)

    async def increment_send_retry(self, receipt_no: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            await conn.execute(
                "UPDATE disclosures SET send_retry_count = send_retry_count + 1 WHERE rcept_no = ?",
                (receipt_no,),
            )
            await conn.commit()

    async def get_pending_digest(
        self, receipt_date: str, *, immediate_threshold: int
    ) -> list[StoredDisclosure]:
        return await self._query_stored(
            """
            SELECT * FROM disclosures
            WHERE receipt_date = ? AND importance_score < ? AND digest_sent_at IS NULL
            ORDER BY importance_score DESC, rcept_no ASC
            """,
            (receipt_date, int(immediate_threshold)),
        )

    async def mark_digest_sent(self, receipt_nos: Iterable[str], sent_at: datetime) -> None:
        await self._update_timestamp("digest_sent_at", list(receipt_nos), sent_at)

    async def is_initialized(self) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            async with conn.execute(
                "SELECT value FROM monitor_state WHERE key = 'initialized'"
            ) as cur:
                row = await cur.fetchone()
        return bool(row and row[0] == "1")

    async def mark_initialized(self) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            await conn.execute(
                "INSERT OR REPLACE INTO monitor_state(key, value) VALUES ('initialized', '1')"
            )
            await conn.commit()

    async def _query_stored(self, sql: str, params: tuple) -> list[StoredDisclosure]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await self._setup(conn)
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def _update_timestamp(
        self, column: str, receipt_nos: list[str], sent_at: datetime
    ) -> None:
        if not receipt_nos:
            return
        placeholders = ",".join("?" for _ in receipt_nos)
        async with aiosqlite.connect(self._db_path) as conn:
            await self._setup(conn)
            await conn.execute(
                f"UPDATE disclosures SET {column} = ? WHERE rcept_no IN ({placeholders})",
                (sent_at.isoformat(), *receipt_nos),
            )
            await conn.commit()

    @staticmethod
    def _from_row(row) -> StoredDisclosure:
        return StoredDisclosure(
            disclosure=DartDisclosure(
                corp_class="",
                corp_name=row["corp_name"],
                corp_code=row["corp_code"],
                stock_code=row["stock_code"],
                report_name=row["report_name"],
                receipt_no=row["rcept_no"],
                filer_name=row["filer_name"],
                receipt_date=row["receipt_date"],
                remarks=row["remarks"],
            ),
            importance=DisclosureImportance(
                score=int(row["importance_score"]),
                level=row["importance_level"],
                reasons=json.loads(row["importance_reasons"]),
            ),
        )
