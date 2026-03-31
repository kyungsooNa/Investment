import asyncio
import json
import os
import sqlite3
import time
import threading
import logging
from contextlib import contextmanager
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor


class ProgramTradingStreamService:
    """
    프로그램매매 실시간 데이터의 수신, 저장(SQLite), 클라이언트 전송을 담당하는 서비스.
    - 프로그램매매 히스토리: SQLite pt_history 테이블 (버퍼 기반 일괄 저장)
    - 스냅샷(차트 데이터): SQLite pt_snapshot 테이블
    - 구독 상태: SQLite pt_subscriptions 테이블 (재시작 시 복구)
    """

    RETENTION_DAYS = 7  # 데이터 보존 기간
    FLUSH_INTERVAL_SEC = 1.0   # 버퍼 플러시 주기 (초)
    FLUSH_BATCH_SIZE = 100     # 이 개수 이상이면 즉시 플러시

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)

        # 메모리 캐시 (성능 최적화)
        self._pt_history: dict = {}  # {code: [data1, data2, ...]}
        self.last_data_ts = 0.0      # 마지막 데이터 수신 시간 (Timestamp)

        # 클라이언트 스트리밍
        self._pt_queues: list = []  # 접속한 클라이언트들의 큐 리스트
        self._pt_codes: set = set()  # 현재 구독 중인 종목 코드 집합

        # SQLite
        self._db_path = os.path.join(self._get_base_dir(), "program_trading.db")
        self._lock = threading.Lock()
        self._conn = None

        # 버퍼 기반 일괄 저장
        self._write_buffer: list = []
        self._buffer_lock = threading.Lock()
        self._flush_task: asyncio.Task = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pt_db")

        # 초기화
        os.makedirs(self._get_base_dir(), exist_ok=True)
        self._init_db()
        self._load_subscribed_codes()
        self._load_pt_history()

    # --- SQLite 초기화 ---
    def _get_base_dir(self):
        return "data/program_subscribe"

    def _init_db(self):
        """SQLite DB 초기화 및 테이블 생성."""
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                # 프로그램매매 히스토리 테이블 (모든 필드 분리 및 정수형 최적화)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code TEXT NOT NULL,
                        trade_time TEXT,       -- 주식체결시간
                        price INTEGER DEFAULT 0, -- 현재가
                        rate REAL DEFAULT 0.0,   -- 등락률
                        sell_vol INTEGER,      -- 매도체결량
                        sell_amt INTEGER,      -- 매도거래대금
                        buy_vol INTEGER,       -- 매수2체결량
                        buy_amt INTEGER,       -- 매수2거래대금
                        net_vol INTEGER,       -- 순매수체결량
                        net_amt INTEGER,       -- 순매수거래대금
                        sell_rem INTEGER,      -- 매도호가잔량
                        buy_rem INTEGER,       -- 매수호가잔량
                        net_rem INTEGER,       -- 전체순매수호가잔량
                        created_at REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_code ON pt_history(code)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_created_at ON pt_history(created_at)")

                # 기존 테이블 마이그레이션: price 컬럼 추가
                try:
                    conn.execute("ALTER TABLE pt_history ADD COLUMN price INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass

                # 기존 테이블 마이그레이션: rate 컬럼 추가
                try:
                    conn.execute("ALTER TABLE pt_history ADD COLUMN rate REAL DEFAULT 0.0")
                except sqlite3.OperationalError:
                    pass

                # 구독 상태 테이블
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_subscriptions (
                        code TEXT PRIMARY KEY
                    )
                """)

                # 스냅샷 테이블
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_snapshot (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
            self.logger.info("ProgramTradingStreamService: SQLite DB 초기화 완료")
        except Exception as e:
            self.logger.error(f"SQLite DB 초기화 실패: {e}")

    @contextmanager
    def _get_connection(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # --- 데이터 로드 ---
    def _load_pt_history(self):
        """DB에서 금일 프로그램 매매 이력을 메모리로 로드."""
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

                    # 기존 웹 UI 및 타 서비스와 호환되도록 원래 JSON 구조로 완벽히 복원
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
                        "전체순매수호가잔량": str(net_rem)
                    }
                    self._pt_history.setdefault(code, []).append(restored_data)
                    count += 1

            if count > 0:
                self.logger.info(f"DB에서 {count}건의 히스토리 데이터를 복구했습니다.")
        except Exception as e:
            self.logger.error(f"히스토리 로드 중 오류: {e}")

    def _load_subscribed_codes(self):
        """DB에서 구독 상태를 복원."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT code FROM pt_subscriptions")
                codes = [row[0] for row in cursor.fetchall()]
                self._pt_codes = set(codes)
            if self._pt_codes:
                self.logger.info(f"구독 상태 복원: {sorted(self._pt_codes)}")
        except Exception as e:
            self.logger.error(f"구독 상태 로드 실패: {e}")

    # --- 데이터 처리 ---
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

    def on_data_received(self, data: dict):
        """웹소켓 등에서 수신한 데이터를 처리 (버퍼 저장 및 브로드캐스트).

        DB 쓰기는 버퍼에 적재 후 백그라운드에서 일괄 처리하여
        이벤트 루프 블로킹을 방지한다.
        """
        code = data.get('유가증권단축종목코드')
        if not code:
            return

        now = time.time()
        if self.last_data_ts > 0 and (now - self.last_data_ts) > 10.0:
            self.logger.info(f"실시간 데이터 수신 재개 (Gap: {now - self.last_data_ts:.1f}초)")

        self.last_data_ts = now

        # 1. 메모리 저장 (기존 프론트엔드/백엔드 호환용 원본 유지)
        self._pt_history.setdefault(code, []).append(data)

        # 2. 버퍼에 적재 (이벤트 루프 블로킹 방지)
        trade_time = data.get('주식체결시간', '')
        price = self._safe_int(data.get('price', 0))
        rate = self._safe_float(data.get('rate', 0.0))
        sell_vol = self._safe_int(data.get('매도체결량', 0))
        sell_amt = self._safe_int(data.get('매도거래대금', 0))
        buy_vol = self._safe_int(data.get('매수2체결량', 0))
        buy_amt = self._safe_int(data.get('매수2거래대금', 0))
        net_vol = self._safe_int(data.get('순매수체결량', 0))
        net_amt = self._safe_int(data.get('순매수거래대금', 0))
        sell_rem = self._safe_int(data.get('매도호가잔량', 0))
        buy_rem = self._safe_int(data.get('매수호가잔량', 0))
        net_rem = self._safe_int(data.get('전체순매수호가잔량', 0))

        row = (code, trade_time, price, rate, sell_vol, sell_amt, buy_vol, buy_amt,
               net_vol, net_amt, sell_rem, buy_rem, net_rem, now)
        with self._buffer_lock:
            self._write_buffer.append(row)

        # 3. 클라이언트 브로드캐스트 (Dict 대신 Array 전송으로 네트워크 트래픽 80% 절감)
        payload = [
            code,
            trade_time,
            price,
            rate,
            data.get('change', 0),
            data.get('sign', ''),
            sell_vol,
            buy_vol,
            net_vol,
            net_amt,
            sell_rem,
            buy_rem
        ]

        json_payload = json.dumps(payload, ensure_ascii=False)
        for q in list(self._pt_queues):
            try:
                q.put_nowait(json_payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(json_payload)
                except Exception:
                    pass
            except Exception:
                pass

    # --- 버퍼 플러시 ---
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
                self.logger.error(f"벌크 인서트 실패: {e}")
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
        except asyncio.CancelledError:
            # 종료 시 잔여 데이터 플러시
            await self._flush_write_buffer()

    # --- 클라이언트 큐 관리 ---
    def create_subscriber_queue(self) -> asyncio.Queue:
        """새로운 구독자(웹 클라이언트)를 위한 큐 생성 및 등록."""
        queue = asyncio.Queue(maxsize=200)
        self._pt_queues.append(queue)
        return queue

    def remove_subscriber_queue(self, queue: asyncio.Queue):
        """구독자 큐 제거."""
        if queue in self._pt_queues:
            self._pt_queues.remove(queue)

    def get_history_data(self):
        """현재 메모리에 있는 히스토리 데이터 반환."""
        return self._pt_history

    # --- 구독 상태 관리 (SQLite 영속) ---
    def add_subscribed_code(self, code: str):
        self._pt_codes.add(code)
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pt_subscriptions (code) VALUES (?)",
                    (code,)
                )
        except Exception as e:
            self.logger.error(f"구독 상태 저장 실패: {e}")

    def remove_subscribed_code(self, code: str):
        self._pt_codes.discard(code)
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM pt_subscriptions WHERE code = ?", (code,))
        except Exception as e:
            self.logger.error(f"구독 상태 삭제 실패: {e}")

    def clear_subscribed_codes(self):
        self._pt_codes.clear()
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM pt_subscriptions")
        except Exception as e:
            self.logger.error(f"구독 상태 전체 삭제 실패: {e}")

    def is_subscribed(self, code: str) -> bool:
        return code in self._pt_codes

    def get_subscribed_codes(self) -> list:
        return sorted(list(self._pt_codes))

    # --- 스냅샷 저장/로드 (SQLite) ---
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
            self.logger.error(f"스냅샷 저장 실패: {e}")
            raise e

    def load_snapshot(self) -> dict:
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT value, updated_at FROM pt_snapshot WHERE key = ?", ("pt_data",))
                row = cursor.fetchone()
                if row:
                    updated_at = datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M:%S")
                    self.logger.info(f"스냅샷 로드됨 (Last Updated: {updated_at})")
                    return json.loads(row[0])
        except Exception as e:
            self.logger.error(f"스냅샷 로드 실패: {e}")
        return None

    # --- 데이터 정리 ---
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
                    self.logger.info(f"{self.RETENTION_DAYS}일 이전 히스토리 {deleted}건 삭제 완료")
                    conn.execute("PRAGMA optimize")
        except Exception as e:
            self.logger.error(f"오래된 데이터 정리 중 오류: {e}")

    def _cleanup_old_files(self):
        """기존 JSONL/JSON 파일 정리 (마이그레이션 후 잔여 파일 삭제)."""
        try:
            base_dir = self._get_base_dir()
            for filename in os.listdir(base_dir):
                if filename.endswith(".jsonl") or filename == "pt_data.json":
                    file_path = os.path.join(base_dir, filename)
                    os.remove(file_path)
                    self.logger.info(f"레거시 파일 삭제: {filename}")
        except Exception as e:
            self.logger.error(f"레거시 파일 정리 중 오류: {e}")

    # --- 생명주기 관리 ---
    def start_background_tasks(self):
        """백그라운드 태스크 시작 (데이터 정리 + 버퍼 플러시 루프)."""
        self._cleanup_old_data()
        self._cleanup_old_files()
        # 비동기 플러시 루프 시작 (async 컨텍스트에서 호출됨)
        self._flush_task = asyncio.create_task(self._flush_loop())
        self.logger.info("ProgramTradingStreamService: 초기화 완료 (버퍼 기반 일괄 저장 모드)")

    async def shutdown(self):
        """서비스 종료 처리."""
        # 플러시 태스크 종료
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # 잔여 버퍼 동기 플러시
        self.flush_write_buffer_sync()

        # executor 종료
        self._executor.shutdown(wait=False)

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self.logger.info("ProgramTradingStreamService: 종료 완료")

    def inspect_db_status(self) -> dict:
        """DB 상태(마지막 저장 시간, 데이터 건수 등)를 조회하여 반환 (디버깅용)."""
        status = {
            "snapshot": {"exists": False, "updated_at": None},
            "history": {"count": 0, "last_record": None, "hourly_counts": {}},
            "memory": {"last_received_at": None}
        }
        try:
            with self._get_connection() as conn:
                # 1. Snapshot 확인
                cursor = conn.execute("SELECT updated_at FROM pt_snapshot WHERE key='pt_data'")
                row = cursor.fetchone()
                if row:
                    status["snapshot"]["exists"] = True
                    status["snapshot"]["updated_at"] = datetime.fromtimestamp(row[0]).strftime("%Y-%m-%d %H:%M:%S")

                # 2. History 확인 (오늘 기준)
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

                cursor = conn.execute(
                    "SELECT COUNT(*), MAX(created_at) FROM pt_history WHERE created_at >= ?",
                    (today_start,)
                )
                cnt, last_ts = cursor.fetchone()
                status["history"]["count"] = cnt
                if last_ts:
                    status["history"]["last_record"] = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")

                # 시간대별 건수
                cursor = conn.execute("""
                    SELECT strftime('%H', datetime(created_at, 'unixepoch', 'localtime')) as hour, count(*)
                    FROM pt_history
                    WHERE created_at >= ?
                    GROUP BY hour
                    ORDER BY hour
                """, (today_start,))

                for r in cursor.fetchall():
                    status["history"]["hourly_counts"][r[0]] = r[1]

            # 메모리 상태 추가
            if self.last_data_ts > 0:
                status["memory"]["last_received_at"] = datetime.fromtimestamp(self.last_data_ts).strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            self.logger.error(f"DB 검사 중 오류: {e}")
            status["error"] = str(e)

        return status
