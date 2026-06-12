# scheduler/strategy_scheduler_store.py
"""SQLite 기반 스케줄러 영속화 스토어.

signal_history (시그널 실행 이력) 와 scheduler_state (활성 전략/설정) 를
단일 SQLite DB 파일에 ACID 트랜잭션으로 저장한다.

레거시 파일이 존재하면 최초 1회 DB로 마이그레이션 후 .migrated 로 이름 변경.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import threading
from typing import Optional

SCHEDULER_DB_FILE = "data/StrategyScheduler/scheduler.db"
_LEGACY_SIGNAL_CSV = "data/StrategyScheduler/signal_history.csv"
_LEGACY_STATE_JSON = "data/StrategyScheduler/scheduler_state.json"

_DDL_SIGNAL_HISTORY = """
CREATE TABLE IF NOT EXISTS signal_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT    NOT NULL,
    code          TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    price         INTEGER NOT NULL,
    qty           INTEGER NOT NULL DEFAULT 1,
    return_rate   REAL,
    reason        TEXT,
    timestamp     TEXT    NOT NULL,
    api_success   INTEGER NOT NULL DEFAULT 1,
    trace_id      TEXT    NOT NULL DEFAULT ''
);
"""

_DDL_SCHEDULER_STATE = """
CREATE TABLE IF NOT EXISTS scheduler_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StrategySchedulerStore:
    """signal_history + scheduler_state 를 SQLite 에 영속화."""

    def __init__(
        self,
        db_path: str = SCHEDULER_DB_FILE,
        logger: Optional[logging.Logger] = None,
    ):
        self._db_path = db_path
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = self._init_db()
        self._migrate_legacy_files()

    # ── 초기화 ──

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_DDL_SIGNAL_HISTORY)
        conn.execute(_DDL_SCHEDULER_STATE)
        self._ensure_signal_history_columns(conn)
        conn.commit()
        return conn

    @staticmethod
    def _ensure_signal_history_columns(conn: sqlite3.Connection) -> None:
        """기존 DB에 누락된 컬럼을 ALTER 로 보강한다 (trace_id, 2026-06-12 추가)."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signal_history)")}
        if "trace_id" not in cols:
            conn.execute(
                "ALTER TABLE signal_history ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ── Signal History ──

    def append_signal(self, record) -> None:
        """SignalRecord 1건을 DB에 삽입."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO signal_history
                   (strategy_name, code, name, action, price, qty,
                    return_rate, reason, timestamp, api_success, trace_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.strategy_name, record.code, record.name, record.action,
                    record.price, record.qty, record.return_rate, record.reason,
                    record.timestamp, 1 if record.api_success else 0,
                    getattr(record, "trace_id", "") or "",
                ),
            )
            self._conn.commit()

    def load_signal_history(self, limit: int = 200) -> list:
        """최근 N건 시그널 이력을 오래된 순 dict list 로 반환."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT strategy_name, code, name, action, price, qty,
                          return_rate, reason, timestamp, api_success, trace_id
                   FROM signal_history
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            rows = cur.fetchall()
        return [
            {
                "strategy_name": r[0], "code": r[1], "name": r[2],
                "action": r[3], "price": r[4], "qty": r[5],
                "return_rate": r[6], "reason": r[7],
                "timestamp": r[8], "api_success": bool(r[9]),
                "trace_id": r[10] or "",
            }
            for r in reversed(rows)
        ]

    def load_signal_history_for_date(self, target_date: str) -> list:
        """target_date(YYYYMMDD)의 시그널 이력을 오래된 순 dict list 로 반환."""
        digits = "".join(ch for ch in str(target_date or "") if ch.isdigit())
        if len(digits) < 8:
            return []
        date_prefix = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        with self._lock:
            cur = self._conn.execute(
                """SELECT strategy_name, code, name, action, price, qty,
                          return_rate, reason, timestamp, api_success, trace_id
                   FROM signal_history
                   WHERE timestamp LIKE ?
                   ORDER BY timestamp ASC, id ASC""",
                (f"{date_prefix}%",),
            )
            rows = cur.fetchall()
        return [
            {
                "strategy_name": r[0], "code": r[1], "name": r[2],
                "action": r[3], "price": r[4], "qty": r[5],
                "return_rate": r[6], "reason": r[7],
                "timestamp": r[8], "api_success": bool(r[9]),
                "trace_id": r[10] or "",
            }
            for r in rows
        ]

    # ── Scheduler State ──

    def save_state(self, state: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO scheduler_state (key, value) VALUES ('state', ?)",
                (json.dumps(state, ensure_ascii=False),),
            )
            self._conn.commit()

    def load_state(self) -> Optional[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM scheduler_state WHERE key = 'state'"
            )
            row = cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    def clear_state(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scheduler_state WHERE key = 'state'")
            self._conn.commit()

    def save_keyed(self, key: str, value: str) -> None:
        """임의 키로 단일 문자열 값 저장."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO scheduler_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()

    def load_keyed(self, key: str) -> Optional[str]:
        """임의 키로 저장된 문자열 값 로드. 없으면 None 반환."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM scheduler_state WHERE key = ?", (key,)
            )
            row = cur.fetchone()
        return row[0] if row else None

    # ── 레거시 파일 마이그레이션 ──

    def _migrate_legacy_files(self) -> None:
        self._migrate_csv()
        self._migrate_json()

    def _migrate_csv(self) -> None:
        if not os.path.exists(_LEGACY_SIGNAL_CSV):
            return
        try:
            with open(_LEGACY_SIGNAL_CSV, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            with self._lock:
                cur = self._conn.execute("SELECT COUNT(*) FROM signal_history")
                if cur.fetchone()[0] > 0:
                    os.rename(_LEGACY_SIGNAL_CSV, _LEGACY_SIGNAL_CSV + ".migrated")
                    return

                for row in rows:
                    rv = row.get("return_rate")
                    try:
                        return_rate: Optional[float] = float(rv) if rv else None
                    except (ValueError, TypeError):
                        return_rate = None
                    try:
                        self._conn.execute(
                            """INSERT INTO signal_history
                               (strategy_name, code, name, action, price, qty,
                                return_rate, reason, timestamp, api_success)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                row["strategy_name"], row["code"], row["name"], row["action"],
                                int(row["price"]), int(row.get("qty") or 1), return_rate,
                                row["reason"], row["timestamp"],
                                1 if row.get("api_success", "True") == "True" else 0,
                            ),
                        )
                    except Exception as e:
                        self._logger.warning(f"[Store] CSV 행 마이그레이션 실패: {e}")
                self._conn.commit()

            os.rename(_LEGACY_SIGNAL_CSV, _LEGACY_SIGNAL_CSV + ".migrated")
            self._logger.info(f"[Store] signal_history.csv → DB 마이그레이션 완료 ({len(rows)}건)")
        except Exception as e:
            self._logger.error(f"[Store] CSV 마이그레이션 실패: {e}")

    def _migrate_json(self) -> None:
        if not os.path.exists(_LEGACY_STATE_JSON):
            return
        try:
            with open(_LEGACY_STATE_JSON, "r", encoding="utf-8") as f:
                state = json.load(f)

            with self._lock:
                cur = self._conn.execute("SELECT COUNT(*) FROM scheduler_state")
                if cur.fetchone()[0] > 0:
                    os.rename(_LEGACY_STATE_JSON, _LEGACY_STATE_JSON + ".migrated")
                    return

                self._conn.execute(
                    "INSERT OR REPLACE INTO scheduler_state (key, value) VALUES ('state', ?)",
                    (json.dumps(state, ensure_ascii=False),),
                )
                self._conn.commit()

            os.rename(_LEGACY_STATE_JSON, _LEGACY_STATE_JSON + ".migrated")
            self._logger.info("[Store] scheduler_state.json → DB 마이그레이션 완료")
        except Exception as e:
            self._logger.error(f"[Store] JSON 상태 마이그레이션 실패: {e}")
