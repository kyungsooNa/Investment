"""체결 틱에 포함된 최우선 호가·잔량 장중 시계열 SQLite 저장소."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any, Optional


class OrderbookSnapshotRepository:
    """H0STCNT0/H0UNCNT0 체결 틱의 top-of-book을 종목당 샘플링한다.

    별도 H0STASP0 구독 없이 기존 PRICE 틱의 최우선 매도·매수호가와 잔량을
    저장한다. 따라서 WebSocket 슬롯을 추가로 소비하지 않는다.
    """

    DEFAULT_BASE_DIR = "data/orderbook_snapshots"
    SAMPLE_INTERVAL_SEC = 60.0
    FLUSH_INTERVAL_SEC = 30.0
    FLUSH_BUFFER_SIZE = 20
    RETENTION_DAYS = 30

    def __init__(
        self,
        base_dir: str = DEFAULT_BASE_DIR,
        logger=None,
        sample_interval_sec: Optional[float] = None,
        retention_days: Optional[int] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._base_dir = base_dir
        self._db_path = os.path.join(base_dir, "orderbook_snapshots.db")
        self._sample_interval_sec = (
            sample_interval_sec
            if sample_interval_sec is not None
            else self.SAMPLE_INTERVAL_SEC
        )
        self._retention_days = (
            retention_days if retention_days is not None else self.RETENTION_DAYS
        )
        self._conn: Optional[sqlite3.Connection] = None
        self._buffer: list[tuple] = []
        self._buffer_lock = threading.Lock()
        self._last_sampled: dict[str, float] = {}
        self._last_flush = time.time()

        os.makedirs(self._base_dir, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS top_of_book_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        trade_time TEXT NOT NULL,
                        ask_price INTEGER NOT NULL,
                        bid_price INTEGER NOT NULL,
                        ask_qty INTEGER,
                        bid_qty INTEGER,
                        total_ask_qty INTEGER,
                        total_bid_qty INTEGER,
                        created_at REAL NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_top_of_book_code_date "
                    "ON top_of_book_history(code, trade_date)"
                )
                cutoff = time.time() - self._retention_days * 86400
                self._conn.execute(
                    "DELETE FROM top_of_book_history WHERE created_at < ?", (cutoff,)
                )
        except sqlite3.Error as exc:
            self._logger.error(f"OrderbookSnapshotRepository DB 초기화 실패: {exc}")
            self._conn = None

    def record_tick(
        self,
        code: str,
        realtime_data: dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> bool:
        """유효한 최우선 호가 스냅샷을 샘플링 버퍼에 채택하면 True."""
        if self._conn is None or not code or not isinstance(realtime_data, dict):
            return False
        ask_price = self._parse_int(realtime_data.get("매도호가1"))
        bid_price = self._parse_int(realtime_data.get("매수호가1"))
        trade_time = self._normalize_digits(realtime_data.get("주식체결시간"), 6)
        if (
            ask_price is None
            or bid_price is None
            or ask_price <= 0
            or bid_price <= 0
            or ask_price < bid_price
            or trade_time is None
        ):
            return False
        if now is None:
            now = time.time()
        last = self._last_sampled.get(code)
        if last is not None and (now - last) < self._sample_interval_sec:
            return False
        trade_date = self._normalize_digits(realtime_data.get("영업일자"), 8) or time.strftime(
            "%Y%m%d", time.localtime(now)
        )
        row = (
            code,
            trade_date,
            trade_time,
            ask_price,
            bid_price,
            self._parse_int(realtime_data.get("매도호가잔량")),
            self._parse_int(realtime_data.get("매수호가잔량")),
            self._parse_int(realtime_data.get("총매도호가잔량")),
            self._parse_int(realtime_data.get("총매수호가잔량")),
            now,
        )
        self._last_sampled[code] = now
        with self._buffer_lock:
            self._buffer.append(row)
            should_flush = (
                len(self._buffer) >= self.FLUSH_BUFFER_SIZE
                or (now - self._last_flush) >= self.FLUSH_INTERVAL_SEC
            )
        if should_flush:
            self.flush()
        return True

    def flush(self) -> None:
        if self._conn is None:
            return
        with self._buffer_lock:
            batch = self._buffer
            self._buffer = []
            self._last_flush = time.time()
        if not batch:
            return
        try:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO top_of_book_history "
                    "(code, trade_date, trade_time, ask_price, bid_price, ask_qty, "
                    "bid_qty, total_ask_qty, total_bid_qty, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
        except sqlite3.Error as exc:
            self._logger.error(f"OrderbookSnapshotRepository flush 실패: {exc}")

    def close(self) -> None:
        self.flush()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value not in (None, "", "N/A") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_digits(value: Any, length: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text if len(text) == length and text.isdigit() else None
