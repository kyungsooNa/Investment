# repositories/program_trading_repo.py
"""
프로그램매매 실시간 데이터의 SQLite 저장/조회를 담당하는 저장소.

역할:
  - SQLite DB 초기화 (pt_history, pt_subscriptions, pt_snapshot 테이블)
  - 수신 데이터 버퍼 관리 및 주기적 일괄 저장 (ThreadPoolExecutor + asyncio.Task)
  - 금일 히스토리 메모리 복원 (앱 시작 시)
  - 스냅샷 저장/조회 (pt_snapshot)
  - 오래된 데이터 정리

구독 상태(pt_subscriptions) 영속화:
  StreamingStockRepo가 단일 진실 공급원(SSOT)으로 pt_subscriptions 테이블을 직접 관리한다.
  ProgramTradingRepo는 테이블 생성만 담당하며 구독 상태 읽기/쓰기는 수행하지 않는다.

동시성:
  - 메인 스레드(WebSocket 콜백): 버퍼 적재 (_buffer_lock)
  - ThreadPoolExecutor 워커: DB 일괄 삽입 (_lock)
  - asyncio 이벤트 루프: 버퍼 플러시 스케줄링

ProgramTradingStreamService와의 역할 구분:
  - ProgramTradingRepo          : SQLite 저장·조회·버퍼링 (데이터 레이어)
  - ProgramTradingStreamService : 데이터 수신·브로드캐스트·메모리 캐시 (스트림 레이어)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from typing import Optional


class ProgramTradingRepo:
    """프로그램매매 SQLite 저장소."""

    RETENTION_DAYS = 7
    FLUSH_INTERVAL_SEC = 1.0
    FLUSH_BATCH_SIZE = 100

    def __init__(self, base_dir: str = "data/program_subscribe", logger=None, streaming_stock_repo=None):
        self._logger = logger or logging.getLogger(__name__)
        self._base_dir = base_dir
        self._db_path = os.path.join(base_dir, "program_trading.db")

        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        self._write_buffer: list = []
        self._buffer_lock = threading.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pt_db")
        self._streaming_stock_repo = streaming_stock_repo

        os.makedirs(self._base_dir, exist_ok=True)
        self._init_db()

    # ── DB 초기화 ────────────────────────────────────────────────────

    def _init_db(self):
        """SQLite DB 초기화 및 테이블 생성."""
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code TEXT NOT NULL,
                        trade_time TEXT,
                        price INTEGER DEFAULT 0,
                        rate REAL DEFAULT 0.0,
                        sell_vol INTEGER,
                        sell_amt INTEGER,
                        buy_vol INTEGER,
                        buy_amt INTEGER,
                        net_vol INTEGER,
                        net_amt INTEGER,
                        sell_rem INTEGER,
                        buy_rem INTEGER,
                        net_rem INTEGER,
                        created_at REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_code ON pt_history(code)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_created_at ON pt_history(created_at)")

                # 기존 테이블 마이그레이션
                try:
                    conn.execute("ALTER TABLE pt_history ADD COLUMN price INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE pt_history ADD COLUMN rate REAL DEFAULT 0.0")
                except sqlite3.OperationalError:
                    pass

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_snapshot (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
            self._logger.info("ProgramTradingRepo: SQLite DB 초기화 완료")
        except Exception as e:
            self._logger.error(f"SQLite DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ── 데이터 로드 ──────────────────────────────────────────────────

    def load_today_history(self) -> dict:
        """DB에서 금일 프로그램 매매 이력을 dict로 반환."""
        result: dict = {}
        try:
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_ts = today_start.timestamp()

            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT
                        code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
                        net_vol, net_amt, sell_rem, buy_rem, net_rem
                    FROM pt_history
                    WHERE created_at >= ? ORDER BY id ASC
                """, (today_ts,))

                count = 0
                for row in cursor.fetchall():
                    (code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
                     net_vol, net_amt, sell_rem, buy_rem, net_rem) = row
                    restored_data = {
                        "유가증권단축종목코드": code,
                        "주식체결시간": trade_time,
                        "price": price,
                        "rate": rate,
                        "매도체결량": str(sell_vol),
                        "매도거래대금": str(sell_amt),
                        "매수2체결량": str(buy_vol),
                        "매수2거래대금": str(buy_amt),
                        "순매수체결량": str(net_vol),
                        "순매수거래대금": str(net_amt),
                        "매도호가잔량": str(sell_rem),
                        "매수호가잔량": str(buy_rem),
                        "전체순매수호가잔량": str(net_rem),
                    }
                    result.setdefault(code, []).append(restored_data)
                    count += 1

            if count > 0:
                self._logger.info(f"DB에서 {count}건의 히스토리 데이터를 복구했습니다.")
        except Exception as e:
            self._logger.error(f"히스토리 로드 중 오류: {e}")
        return result

    # ── 버퍼 관리 ────────────────────────────────────────────────────

    @staticmethod
    def _safe_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    def add_record_to_buffer(self, data: dict, created_at: float) -> None:
        """수신 데이터에서 필드를 추출해 버퍼에 적재."""
        code = data.get('유가증권단축종목코드')
        row = (
            code,
            data.get('주식체결시간', ''),
            self._safe_int(data.get('price', 0)),
            self._safe_float(data.get('rate', 0.0)),
            self._safe_int(data.get('매도체결량', 0)),
            self._safe_int(data.get('매도거래대금', 0)),
            self._safe_int(data.get('매수2체결량', 0)),
            self._safe_int(data.get('매수2거래대금', 0)),
            self._safe_int(data.get('순매수체결량', 0)),
            self._safe_int(data.get('순매수거래대금', 0)),
            self._safe_int(data.get('매도호가잔량', 0)),
            self._safe_int(data.get('매수호가잔량', 0)),
            self._safe_int(data.get('전체순매수호가잔량', 0)),
            created_at,
        )
        with self._buffer_lock:
            self._write_buffer.append(row)

    def _bulk_insert_to_db(self, batch: list):
        """동기 벌크 인서트 — ThreadPoolExecutor에서 실행."""
        with self._lock:
            try:
                self._conn.executemany("""
                    INSERT INTO pt_history (
                        code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
                        net_vol, net_amt, sell_rem, buy_rem, net_rem, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                self._conn.commit()
            except Exception as e:
                self._logger.error(f"벌크 인서트 실패: {e}")
                try:
                    self._conn.rollback()
                except Exception:
                    pass

    async def _flush_write_buffer(self):
        """버퍼를 꺼내 executor에서 벌크 인서트."""
        with self._buffer_lock:
            if not self._write_buffer:
                return
            batch = list(self._write_buffer)
            self._write_buffer.clear()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._bulk_insert_to_db, batch)

    def flush_write_buffer_sync(self):
        """동기 플러시 — 테스트 또는 종료 시 잔여 데이터를 즉시 DB에 기록."""
        with self._buffer_lock:
            if not self._write_buffer:
                return
            batch = list(self._write_buffer)
            self._write_buffer.clear()
        self._bulk_insert_to_db(batch)

    async def _flush_loop(self):
        """주기적으로 버퍼를 플러시하는 백그라운드 태스크."""
        try:
            while True:
                await asyncio.sleep(self.FLUSH_INTERVAL_SEC)
                await self._flush_write_buffer()
                if self._streaming_stock_repo is not None:
                    self._streaming_stock_repo.flush_pt_desired_sync()
        except asyncio.CancelledError:
            await self._flush_write_buffer()
            if self._streaming_stock_repo is not None:
                self._streaming_stock_repo.flush_pt_desired_sync()

    # ── 스냅샷 저장/조회 ─────────────────────────────────────────────

    def save_snapshot(self, data_dict: dict):
        try:
            json_str = json.dumps(data_dict, ensure_ascii=False)
            now = time.time()
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO pt_snapshot (key, value, updated_at) VALUES (?, ?, ?)",
                    ("pt_data", json_str, now)
                )
            return True
        except Exception as e:
            self._logger.error(f"스냅샷 저장 실패: {e}")
            raise e

    def load_snapshot(self) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT value, updated_at FROM pt_snapshot WHERE key = ?", ("pt_data",)
                )
                row = cursor.fetchone()
                if row:
                    updated_at = datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M:%S")
                    self._logger.info(f"스냅샷 로드됨 (Last Updated: {updated_at})")
                    return json.loads(row[0])
        except Exception as e:
            self._logger.error(f"스냅샷 로드 실패: {e}")
        return None

    # ── 데이터 정리 ──────────────────────────────────────────────────

    def _cleanup_old_data(self):
        """보존 기간(7일)이 지난 데이터를 삭제."""
        try:
            cutoff = time.time() - (self.RETENTION_DAYS * 86400)
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM pt_history WHERE created_at < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                if deleted > 0:
                    self._logger.info(f"{self.RETENTION_DAYS}일 이전 히스토리 {deleted}건 삭제 완료")
                    conn.execute("PRAGMA optimize")
        except Exception as e:
            self._logger.error(f"오래된 데이터 정리 중 오류: {e}")

    def _cleanup_old_files(self):
        """기존 JSONL/JSON 파일 정리 (마이그레이션 후 잔여 파일 삭제)."""
        try:
            for filename in os.listdir(self._base_dir):
                if filename.endswith(".jsonl") or filename == "pt_data.json":
                    file_path = os.path.join(self._base_dir, filename)
                    os.remove(file_path)
                    self._logger.info(f"레거시 파일 삭제: {filename}")
        except Exception as e:
            self._logger.error(f"레거시 파일 정리 중 오류: {e}")

    # ── DB 상태 조회 ─────────────────────────────────────────────────

    def inspect_db_status(self) -> dict:
        """DB 상태(마지막 저장 시간, 데이터 건수 등)를 조회하여 반환 (디버깅용)."""
        status = {
            "snapshot": {"exists": False, "updated_at": None},
            "history": {"count": 0, "last_record": None, "hourly_counts": {}},
            "memory": {"last_received_at": None},
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT updated_at FROM pt_snapshot WHERE key='pt_data'")
                row = cursor.fetchone()
                if row:
                    status["snapshot"]["exists"] = True
                    status["snapshot"]["updated_at"] = datetime.fromtimestamp(row[0]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                today_start = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).timestamp()
                cursor = conn.execute(
                    "SELECT COUNT(*), MAX(created_at) FROM pt_history WHERE created_at >= ?",
                    (today_start,),
                )
                cnt, last_ts = cursor.fetchone()
                status["history"]["count"] = cnt
                if last_ts:
                    status["history"]["last_record"] = datetime.fromtimestamp(last_ts).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                cursor = conn.execute(
                    """
                    SELECT strftime('%H', datetime(created_at, 'unixepoch', 'localtime')) as hour,
                           count(*)
                    FROM pt_history
                    WHERE created_at >= ?
                    GROUP BY hour
                    ORDER BY hour
                    """,
                    (today_start,),
                )
                for r in cursor.fetchall():
                    status["history"]["hourly_counts"][r[0]] = r[1]
        except Exception as e:
            self._logger.error(f"DB 검사 중 오류: {e}")
            status["error"] = str(e)
        return status

    def get_history_timestamps_by_code(
        self,
        codes: list[str],
        start_ts: float,
        end_ts: float,
    ) -> dict[str, list[float]]:
        """지정 종목들의 저장 timestamp를 기간 내에서 조회한다."""
        result = {code: [] for code in codes}
        if not codes:
            return result

        placeholders = ",".join("?" for _ in codes)
        params = [*codes, start_ts, end_ts]
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f"""
                    SELECT code, created_at
                    FROM pt_history
                    WHERE code IN ({placeholders})
                      AND created_at >= ?
                      AND created_at <= ?
                    ORDER BY code, created_at
                    """,
                    params,
                )
                for code, created_at in cursor.fetchall():
                    result.setdefault(code, []).append(created_at)
        except Exception as e:
            self._logger.error(f"히스토리 timestamp 조회 중 오류: {e}")
        return result

    # ── 생명주기 ─────────────────────────────────────────────────────

    def start_flush_loop(self):
        """데이터 정리 실행 후 백그라운드 플러시 루프를 시작한다."""
        self._cleanup_old_data()
        self._cleanup_old_files()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def shutdown(self):
        """플러시 태스크 취소 → 잔여 버퍼 동기 플러시 → 커넥션 종료."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        self.flush_write_buffer_sync()
        if self._streaming_stock_repo is not None:
            self._streaming_stock_repo.flush_pt_desired_sync()
        self._executor.shutdown(wait=False)

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._logger.info("ProgramTradingRepo: 종료 완료")
