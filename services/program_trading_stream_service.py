import asyncio
import json
import time
import logging
from datetime import datetime
from contextlib import contextmanager

from repositories.program_trading_repo import ProgramTradingRepo


class ProgramTradingStreamService:
    """
    프로그램매매 실시간 데이터의 수신, 저장(SQLite), 클라이언트 전송을 담당하는 서비스.

    역할 구분:
      - ProgramTradingRepo          : SQLite 저장·버퍼링·구독 영속 (데이터 레이어)
      - ProgramTradingStreamService : 데이터 수신·메모리 캐시·SSE 브로드캐스트 (스트림 레이어)

    하위 호환성:
      _conn / _executor / _flush_task / _write_buffer / _buffer_lock / _lock 등
      테스트에서 직접 접근하는 내부 속성은 property로 _repo에 포워딩.
    """

    RETENTION_DAYS = 7
    FLUSH_INTERVAL_SEC = 1.0
    FLUSH_BATCH_SIZE = 100

    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)

        # 메모리 캐시 (성능 최적화)
        self._pt_history: dict = {}
        self.last_data_ts = 0.0

        # 클라이언트 스트리밍 큐
        self._pt_queues: list = []

        # SQLite 레이어는 ProgramTradingRepo에 위임
        self._repo = ProgramTradingRepo(
            base_dir=self._get_base_dir(),
            logger=self.logger,
        )

        # 시작 시 DB에서 금일 히스토리 복원
        self._load_pt_history()

    # ── 기본 디렉토리 (테스트에서 patch 가능하도록 메서드로 유지) ─────

    def _get_base_dir(self):
        return "data/program_subscribe"

    # ── 내부 속성 포워딩 (하위 호환성 — 기존 테스트 무변경) ──────────

    @property
    def _conn(self):
        return self._repo._conn

    @_conn.setter
    def _conn(self, value):
        self._repo._conn = value

    @property
    def _executor(self):
        return self._repo._executor

    @property
    def _flush_task(self):
        return self._repo._flush_task

    @_flush_task.setter
    def _flush_task(self, value):
        self._repo._flush_task = value

    @property
    def _write_buffer(self):
        return self._repo._write_buffer

    @property
    def _buffer_lock(self):
        return self._repo._buffer_lock

    @property
    def _lock(self):
        return self._repo._lock

    # ── 내부 메서드 포워딩 ───────────────────────────────────────────

    @contextmanager
    def _get_connection(self):
        with self._repo._get_connection() as conn:
            yield conn

    def _load_pt_history(self):
        self._pt_history = self._repo.load_today_history()

    def _bulk_insert_to_db(self, batch: list):
        self._repo._bulk_insert_to_db(batch)

    async def _flush_write_buffer(self):
        await self._repo._flush_write_buffer()

    def flush_write_buffer_sync(self):
        """동기 플러시 — 테스트 또는 종료 시 잔여 데이터를 즉시 DB에 기록."""
        self._repo.flush_write_buffer_sync()

    async def _flush_loop(self):
        await self._repo._flush_loop()

    def _cleanup_old_data(self):
        self._repo._cleanup_old_data()

    def _cleanup_old_files(self):
        self._repo._cleanup_old_files()

    # ── 데이터 처리 ──────────────────────────────────────────────────

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

        # 2. 버퍼에 적재 (이벤트 루프 블로킹 방지) — repo에 위임
        self._repo.add_record_to_buffer(data, created_at=now)

        # 3. 클라이언트 브로드캐스트 (Dict 대신 Array 전송으로 네트워크 트래픽 80% 절감)
        payload = [
            code,
            data.get('주식체결시간', ''),
            self._safe_int(data.get('price', 0)),
            self._safe_float(data.get('rate', 0.0)),
            data.get('change', 0),
            data.get('sign', ''),
            self._safe_int(data.get('매도체결량', 0)),
            self._safe_int(data.get('매수2체결량', 0)),
            self._safe_int(data.get('순매수체결량', 0)),
            self._safe_int(data.get('순매수거래대금', 0)),
            self._safe_int(data.get('매도호가잔량', 0)),
            self._safe_int(data.get('매수호가잔량', 0)),
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

    # ── 클라이언트 큐 관리 ───────────────────────────────────────────

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

    # ── 스냅샷 저장/로드 (repo 위임) ─────────────────────────────────

    def save_snapshot(self, data_dict: dict):
        return self._repo.save_snapshot(data_dict)

    def load_snapshot(self) -> dict:
        return self._repo.load_snapshot()

    # ── DB 상태 조회 (repo 위임 + 메모리 상태 추가) ────────────────────

    def inspect_db_status(self) -> dict:
        """DB 상태(마지막 저장 시간, 데이터 건수 등)를 조회하여 반환 (디버깅용)."""
        status = self._repo.inspect_db_status()
        if self.last_data_ts > 0:
            status["memory"]["last_received_at"] = datetime.fromtimestamp(
                self.last_data_ts
            ).strftime("%Y-%m-%d %H:%M:%S")
        return status

    # ── 생명주기 관리 ────────────────────────────────────────────────

    def start_background_tasks(self):
        """백그라운드 태스크 시작 (데이터 정리 + 버퍼 플러시 루프)."""
        self._repo.start_flush_loop()
        self.logger.info("ProgramTradingStreamService: 초기화 완료 (버퍼 기반 일괄 저장 모드)")

    async def shutdown(self):
        """서비스 종료 처리."""
        await self._repo.shutdown()
        self.logger.info("ProgramTradingStreamService: 종료 완료")
