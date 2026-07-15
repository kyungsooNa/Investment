"""장중 종목별 누적 거래대금 스냅샷 저장소."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Union

import aiosqlite


class ThemeTradingValueSnapshotRepository:
    """최근 구간 거래대금 계산에 필요한 누적값을 SQLite에 보관한다."""

    def __init__(
        self,
        db_path: Union[str, Path] = "data/theme_trading_value_snapshots.db",
    ) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS theme_trading_value_snapshots (
                    captured_at TEXT NOT NULL,
                    code TEXT NOT NULL,
                    cumulative_trading_value_won INTEGER NOT NULL,
                    PRIMARY KEY (captured_at, code)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_theme_tv_code_time "
                "ON theme_trading_value_snapshots(code, captured_at DESC)"
            )

    @staticmethod
    def _time_key(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    async def save_snapshot(self, captured_at: datetime, values: Dict[str, int]) -> None:
        if not values:
            return
        captured_key = self._time_key(captured_at)
        rows = [
            (captured_key, str(code), max(int(value), 0))
            for code, value in values.items()
        ]
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executemany(
                "INSERT OR REPLACE INTO theme_trading_value_snapshots "
                "(captured_at, code, cumulative_trading_value_won) VALUES (?, ?, ?)",
                rows,
            )
            await conn.execute(
                "DELETE FROM theme_trading_value_snapshots WHERE captured_at < ?",
                (self._time_key(captured_at - timedelta(days=1)),),
            )
            await conn.commit()

    async def get_values_at_or_before(
        self,
        target: datetime,
        codes: Iterable[str],
    ) -> Dict[str, int]:
        code_list = sorted({str(code) for code in codes if code})
        if not code_list:
            return {}
        placeholders = ",".join("?" for _ in code_list)
        sql = f"""
            SELECT code, cumulative_trading_value_won
            FROM (
                SELECT code, cumulative_trading_value_won,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY captured_at DESC) AS row_num
                FROM theme_trading_value_snapshots
                WHERE captured_at <= ?
                  AND substr(captured_at, 1, 10) = ?
                  AND code IN ({placeholders})
            )
            WHERE row_num = 1
        """
        params = [self._time_key(target), target.strftime("%Y-%m-%d"), *code_list]
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return {str(code): int(value) for code, value in rows}
