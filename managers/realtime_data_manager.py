import asyncio
import json
import os
import sqlite3
import time
import threading
import logging
from contextlib import contextmanager
from datetime import datetime


class RealtimeDataManager:
    """
    실시간 데이터의 수신, 저장(SQLite), 클라이언트 전송을 담당하는 매니저.
    - 프로그램매매 히스토리: SQLite pt_history 테이블 (즉시 저장, 버퍼 불필요)
    - 스냅샷(차트 데이터): SQLite pt_snapshot 테이블
    - 구독 상태: SQLite pt_subscriptions 테이블 (재시작 시 복구)
    """

    RETENTION_DAYS = 7  # 데이터 보존 기간

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)

        # 메모리 캐시 (성능 최적화)
        self._pt_history: dict = {}  # {code: [data1, data2, ...]}

        # 클라이언트 스트리밍
        self._pt_queues: list = []  # 접속한 클라이언트들의 큐 리스트
        self._pt_codes: set = set()  # 현재 구독 중인 종목 코드 집합

        # SQLite
        self._db_path = os.path.join(self._get_base_dir(), "program_trading.db")
        self._lock = threading.Lock()
        self._conn = None

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
                # 프로그램매매 히스토리 테이블
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_code ON pt_history(code)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_history_created_at ON pt_history(created_at)")

                # 구독 상태 테이블
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_subscriptions (
                        code TEXT PRIMARY KEY
                    )
                """)

                # 스냅샷 테이블 (차트 데이터 저장)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pt_snapshot (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
            self.logger.info("RealtimeDataManager: SQLite DB 초기화 완료")
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
                cursor = conn.execute(
                    "SELECT code, data FROM pt_history WHERE created_at >= ? ORDER BY id ASC",
                    (today_ts,)
                )
                count = 0
                for row in cursor.fetchall():
                    code, data_str = row
                    try:
                        data = json.loads(data_str)
                        self._pt_history.setdefault(code, []).append(data)
                        count += 1
                    except json.JSONDecodeError:
                        continue

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
    def on_data_received(self, data: dict):
        """웹소켓 등에서 수신한 데이터를 처리 (SQLite 즉시 저장 및 브로드캐스트)."""
        code = data.get('유가증권단축종목코드')
        if not code:
            return

        # 1. 메모리 저장
        self._pt_history.setdefault(code, []).append(data)

        # 2. SQLite 즉시 저장 (버퍼 불필요)
        try:
            data_str = json.dumps(data, ensure_ascii=False)
            now = time.time()
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO pt_history (code, data, created_at) VALUES (?, ?, ?)",
                    (code, data_str, now)
                )
        except Exception as e:
            self.logger.error(f"히스토리 DB 저장 실패: {e}")

        # 3. 클라이언트 브로드캐스트
        for q in list(self._pt_queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    pass
            except Exception:
                pass

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
                cursor = conn.execute("SELECT value FROM pt_snapshot WHERE key = ?", ("pt_data",))
                row = cursor.fetchone()
                if row:
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
        """백그라운드 태스크 시작 (데이터 정리)."""
        self._cleanup_old_data()
        self._cleanup_old_files()
        self.logger.info("RealtimeDataManager: 초기화 완료 (SQLite 즉시 저장 모드)")

    async def shutdown(self):
        """서비스 종료 처리."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self.logger.info("RealtimeDataManager: 종료 완료")
