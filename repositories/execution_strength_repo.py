"""체결강도 장중 시계열 SQLite 저장소."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Optional


class ExecutionStrengthRepository:
    """WS 체결 틱(H0STCNT0)의 체결강도를 종목당 샘플링해 SQLite에 축적한다.

    todo 1-5: 체결강도는 EOD REST 스칼라 1개/종목만 확보 가능해 "체결강도 ≥120%"
    장중 게이트의 리플레이가 불가능했다 — 기존 PRICE 틱에 포함된 체결강도를
    장중에 샘플링 저장해 microstructure 캡처(es_db 소스)가 소비한다.

    설계:
      - 신규 WS 구독 없음 — PriceStreamService.on_price_tick 경로에 무임승차.
        (커버리지는 PRICE 구독 + 유틱 종목으로 제한 — 무틱 종목은 캡처 단계에서
         기존 REST 스칼라로 폴백)
      - 백그라운드 태스크/스레드 없음 — 틱 경로에서 버퍼 flush를 amortize한다.
        프로세스 종료 시 마지막 flush 이후 버퍼(샘플링 주기상 종목당 최대 1행
        수준)는 유실될 수 있다.
    """

    DEFAULT_BASE_DIR = "data/execution_strength"
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
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._base_dir = base_dir
        self._db_path = os.path.join(base_dir, "execution_strength.db")
        self._sample_interval_sec = (
            sample_interval_sec if sample_interval_sec is not None else self.SAMPLE_INTERVAL_SEC
        )
        self._retention_days = retention_days if retention_days is not None else self.RETENTION_DAYS

        self._conn: Optional[sqlite3.Connection] = None
        self._buffer: list = []
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
                    CREATE TABLE IF NOT EXISTS es_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        trade_time TEXT NOT NULL,
                        strength REAL NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_es_history_code_date"
                    " ON es_history(code, trade_date)"
                )
                cutoff = time.time() - self._retention_days * 86400
                self._conn.execute("DELETE FROM es_history WHERE created_at < ?", (cutoff,))
        except sqlite3.Error as exc:
            self._logger.error(f"ExecutionStrengthRepository DB 초기화 실패: {exc}")
            self._conn = None

    def record_tick(
        self,
        code: str,
        strength_raw,
        trade_time_raw,
        trade_date_raw=None,
        now: Optional[float] = None,
    ) -> bool:
        """틱 1건을 샘플링 기록한다. 버퍼에 채택되면 True.

        종목당 SAMPLE_INTERVAL_SEC 이내 중복 틱과 파싱 불가 값은 조용히 skip한다.
        """
        if self._conn is None or not code:
            return False
        strength = self._parse_float(strength_raw)
        if strength is None:
            return False
        trade_time = self._normalize_digits(trade_time_raw, 6)
        if trade_time is None:
            return False
        if now is None:
            now = time.time()
        last = self._last_sampled.get(code)
        if last is not None and (now - last) < self._sample_interval_sec:
            return False
        trade_date = self._normalize_digits(trade_date_raw, 8) or time.strftime(
            "%Y%m%d", time.localtime(now)
        )
        self._last_sampled[code] = now
        with self._buffer_lock:
            self._buffer.append((code, trade_date, trade_time, strength, now))
            should_flush = (
                len(self._buffer) >= self.FLUSH_BUFFER_SIZE
                or (now - self._last_flush) >= self.FLUSH_INTERVAL_SEC
            )
        if should_flush:
            self.flush()
        return True

    def flush(self) -> None:
        """버퍼를 DB에 일괄 저장한다."""
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
                    "INSERT INTO es_history (code, trade_date, trade_time, strength, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
        except sqlite3.Error as exc:
            self._logger.error(f"ExecutionStrengthRepository flush 실패: {exc}")

    def close(self) -> None:
        self.flush()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _parse_float(value) -> Optional[float]:
        try:
            return float(value) if value not in (None, "", "N/A") else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_digits(value, length: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) == length and text.isdigit():
            return text
        return None
