"""기간수급(외국인+기관+프로그램 순매수) 랭킹 원천 데이터를 SQLite에 영속화한다.

RankingTask의 in-memory 기간수급 캐시는 재시작 시 소실되고, TimeDispatcher는
거래일당 1회만 티켓을 발행하므로 재시작 후 당일 데이터를 복원하는 용도로 쓴다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Union


class PeriodRankingRepository:
    """기간수급 랭킹 결과를 (거래일, 조회일수) 키로 저장/조회한다."""

    CALCULATION_VERSION = 2

    def __init__(self, db_path: Union[str, Path] = "data/period_ranking.db"):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS period_ranking (
                    trade_date TEXT NOT NULL,
                    days INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculation_version INTEGER NOT NULL DEFAULT 2,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    PRIMARY KEY (trade_date, days)
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(period_ranking)")}
            if "calculation_version" not in columns:
                conn.execute(
                    "ALTER TABLE period_ranking ADD COLUMN "
                    "calculation_version INTEGER NOT NULL DEFAULT 1"
                )

    def save(self, trade_date: str, days: int, results: List[Dict]) -> None:
        """결과를 저장하고 이전 거래일 행은 정리한다 (조회는 항상 최신 거래일 기준)."""
        payload = json.dumps(results, ensure_ascii=False)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO period_ranking "
                "(trade_date, days, results, calculation_version, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now', 'localtime'))",
                (str(trade_date), int(days), payload, self.CALCULATION_VERSION),
            )
            conn.execute(
                "DELETE FROM period_ranking WHERE trade_date < ?",
                (str(trade_date),),
            )

    def get(self, trade_date: str, days: int) -> Optional[List[Dict]]:
        """저장된 결과를 반환한다. 없으면 None."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT results FROM period_ranking "
                "WHERE trade_date = ? AND days = ? AND calculation_version = ?",
                (str(trade_date), int(days), self.CALCULATION_VERSION),
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])
