# repositories/streaming_stock_repo.py
"""
실시간 스트리밍 구독 상태를 중앙 관리하는 저장소.

역할:
  - 구독 대상(desired): 구독해야 할 종목 집합
  - 활성 상태(active): 실제로 브로커에 구독 중인 종목 집합
  - 타입별(UNIFIED_PRICE, PROGRAM_TRADING) 독립 관리
  - PT desired 상태를 SQLite에 영속화 (재시작 시 복원)

동시성:
  - 상태 변경 메서드(mark_*, clear_*, unmark_*)는 asyncio.Lock으로 원자성 보장
  - 읽기 메서드(get_*, is_active)는 snapshot(frozenset) 반환으로 lock-free 안전 접근

StreamingService와의 역할 구분:
  - StreamingStockRepo : "무엇이 구독되어야 하는가" 상태 관리 (데이터 레이어)
  - StreamingService   : "어떻게 구독하는가" 프로토콜 처리 (프로토콜 레이어)
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from enum import Enum
from typing import Dict, Iterable, Optional, Set


class StreamingType(str, Enum):
    """WebSocket 구독 TR_ID 타입."""
    UNIFIED_PRICE = "unified_price"      # H0UNCNT0 — 통합 체결가 (40개 한도)
    PROGRAM_TRADING = "program_trading"  # H0STPGM0 + H0STCNT0 동반 (소수, 제한 없음)


class StreamingStockRepo:
    """
    실시간 구독 상태 중앙 저장소.

    desired  : 구독 요청이 들어온 종목 (구독해야 할 상태)
    active   : 브로커에 실제로 구독이 확인된 종목

    워치독은 get_pending()으로 "desired - active" 종목을 파악하여 재구독 처리한다.
    """

    SOURCE_MANUAL = "manual"
    SOURCE_PROGRAM = "program"
    SOURCE_LEGACY = "legacy"

    def __init__(self, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._desired: Dict[StreamingType, Set[str]] = {t: set() for t in StreamingType}
        self._active: Dict[StreamingType, Set[str]] = {t: set() for t in StreamingType}
        self._lock = asyncio.Lock()

        # PT SQLite 영속화용 (ProgramTradingStreamService의 pt_subscriptions 테이블 재사용)
        self._db_conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        self._db_path: Optional[str] = None

        # 배치 flush용 pending queue — 즉시 commit 대신 flush_pt_desired_sync()에서 일괄 처리
        self._pending_desired_ops: list = []  # list of (code: str, add: bool, source: str)
        self._pending_lock = threading.Lock()
        self._pt_sources: Dict[str, str] = {}

    # ── 초기화 / 영속성 ──────────────────────────────────────────────

    def _ensure_pt_subscription_table_locked(self) -> None:
        """pt_subscriptions 테이블을 보장한다. 호출자는 _db_lock을 보유해야 한다."""
        self._db_conn.execute("""
            CREATE TABLE IF NOT EXISTS pt_subscriptions (
                code TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'legacy',
                updated_at REAL
            )
        """)
        columns = {
            row[1]
            for row in self._db_conn.execute("PRAGMA table_info(pt_subscriptions)").fetchall()
        }
        if "source" not in columns:
            self._db_conn.execute(
                "ALTER TABLE pt_subscriptions ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'"
            )
        if "updated_at" not in columns:
            self._db_conn.execute(
                "ALTER TABLE pt_subscriptions ADD COLUMN updated_at REAL"
            )

    @classmethod
    def _normalize_source(cls, source: Optional[str]) -> str:
        if source == cls.SOURCE_PROGRAM:
            return cls.SOURCE_PROGRAM
        if source == cls.SOURCE_MANUAL:
            return cls.SOURCE_MANUAL
        return cls.SOURCE_LEGACY

    @staticmethod
    def _normalize_codes(codes: Optional[Iterable[str]]) -> Set[str]:
        if not codes:
            return set()
        normalized = set()
        for code in codes:
            if code is None:
                continue
            text = str(code).strip()
            if text:
                normalized.add(text)
        return normalized

    def load_pt_desired_from_db(
        self,
        db_path: str,
        fallback_codes: Optional[Iterable[str]] = None,
    ) -> None:
        """앱 시작 시 SQLite pt_subscriptions 테이블에서 PT desired 상태를 복원.

        구버전 DB처럼 테이블이 없으면 생성한다. DB에 저장된 desired가 아직 없고
        스냅샷에 구독 코드가 남아 있으면 1회 이관해 UI 상태와 실제 구독 SSOT를 맞춘다.
        """
        self._db_path = db_path
        try:
            self._db_conn = sqlite3.connect(db_path, check_same_thread=False)
            with self._db_lock:
                self._ensure_pt_subscription_table_locked()
                cursor = self._db_conn.execute("SELECT code, source, updated_at FROM pt_subscriptions")
                source_rows = {}
                for code, source, updated_at in cursor.fetchall():
                    normalized_source = self._normalize_source(source)
                    if normalized_source == self.SOURCE_MANUAL and updated_at is None:
                        normalized_source = self.SOURCE_LEGACY
                        self._db_conn.execute(
                            "UPDATE pt_subscriptions SET source = ? WHERE code = ?",
                            (normalized_source, code),
                        )
                    source_rows[code] = normalized_source
                codes = set(source_rows)
                if not codes:
                    codes = self._normalize_codes(fallback_codes)
                    if codes:
                        now = time.time()
                        self._db_conn.executemany(
                            """
                            INSERT OR IGNORE INTO pt_subscriptions (code, source, updated_at)
                            VALUES (?, ?, ?)
                            """,
                            [(code, self.SOURCE_LEGACY, now) for code in sorted(codes)],
                        )
                        source_rows = {code: self.SOURCE_LEGACY for code in codes}
                self._db_conn.commit()
            # desired 초기화 (lock 불필요 — 앱 시작 단계, 단일 스레드)
            self._desired[StreamingType.PROGRAM_TRADING] = codes
            self._pt_sources = source_rows
            if codes:
                self._logger.info(f"StreamingStockRepo: PT desired 복원 {sorted(codes)}")
        except Exception as e:
            self._logger.warning(f"StreamingStockRepo: PT desired DB 복원 실패 (무시): {e}")

    def _persist_pt_desired(self, code: str, add: bool, source: str = "manual") -> None:
        """PT desired 변경을 pending queue에 적재. flush_pt_desired_sync()에서 일괄 커밋."""
        if self._db_conn is None:
            return
        with self._pending_lock:
            self._pending_desired_ops.append((code, add, self._normalize_source(source)))

    def flush_pt_desired_sync(self) -> None:
        """pending queue의 PT desired 변경을 1회 트랜잭션으로 일괄 커밋."""
        if self._db_conn is None:
            return
        with self._pending_lock:
            if not self._pending_desired_ops:
                return
            ops = list(self._pending_desired_ops)
            self._pending_desired_ops.clear()
        try:
            with self._db_lock:
                self._ensure_pt_subscription_table_locked()
                now = time.time()
                for code, add, source in ops:
                    if add:
                        self._db_conn.execute(
                            """
                            INSERT OR IGNORE INTO pt_subscriptions (code, source, updated_at)
                            VALUES (?, ?, ?)
                            """,
                            (code, source, now),
                        )
                        self._db_conn.execute(
                            """
                            UPDATE pt_subscriptions
                            SET source = ?, updated_at = ?
                            WHERE code = ?
                            """,
                            (source, now, code),
                        )
                    else:
                        self._db_conn.execute(
                            "DELETE FROM pt_subscriptions WHERE code = ?", (code,)
                        )
                self._db_conn.commit()
        except Exception as e:
            self._logger.warning(f"StreamingStockRepo: PT desired 배치 flush 실패: {e}")
            try:
                self._db_conn.rollback()
            except Exception:
                pass

    # ── 상태 변경 (asyncio.Lock 보호) ────────────────────────────────

    async def mark_desired(
        self,
        code: str,
        stream_type: StreamingType,
        source: str = "manual",
    ) -> None:
        """종목을 구독 대상으로 등록한다."""
        normalized_source = self._normalize_source(source)
        async with self._lock:
            self._desired[stream_type].add(code)
            if stream_type == StreamingType.PROGRAM_TRADING:
                self._pt_sources[code] = normalized_source
        if stream_type == StreamingType.PROGRAM_TRADING:
            self._persist_pt_desired(code, add=True, source=normalized_source)

    async def unmark_desired(self, code: str, stream_type: StreamingType) -> None:
        """종목을 구독 대상에서 제거한다."""
        async with self._lock:
            self._desired[stream_type].discard(code)
            if stream_type == StreamingType.PROGRAM_TRADING:
                self._pt_sources.pop(code, None)
        if stream_type == StreamingType.PROGRAM_TRADING:
            self._persist_pt_desired(code, add=False)

    async def mark_active(self, code: str, stream_type: StreamingType) -> None:
        """브로커 구독 성공 후 활성 상태로 전환한다."""
        async with self._lock:
            self._active[stream_type].add(code)

    async def mark_inactive(self, code: str, stream_type: StreamingType) -> None:
        """브로커 구독 해지 후 활성 상태에서 제거한다."""
        async with self._lock:
            self._active[stream_type].discard(code)

    async def clear_active(self, stream_type: StreamingType) -> None:
        """
        재연결 시 호출 — 브로커 연결이 리셋되었으므로 내부 활성 집합을 비운다.
        desired는 유지되므로 이후 _rebalance() / _restore_all_subscriptions() 호출 시
        모든 desired 종목이 신규 구독된다.
        """
        async with self._lock:
            count = len(self._active[stream_type])
            self._active[stream_type].clear()
        self._logger.debug(
            f"StreamingStockRepo: active 초기화 [{stream_type.value}] {count}개 클리어"
        )

    # ── 읽기 (lock-free snapshot) ────────────────────────────────────

    def get_desired(self, stream_type: StreamingType) -> Set[str]:
        """desired 집합의 복사본을 반환한다 (lock-free 안전 읽기)."""
        return set(self._desired[stream_type])

    def get_pt_subscription_sources(self) -> Dict[str, str]:
        """PROGRAM_TRADING desired 종목별 등록 출처를 반환한다."""
        return dict(self._pt_sources)

    def get_active(self, stream_type: StreamingType) -> Set[str]:
        """active 집합의 복사본을 반환한다 (lock-free 안전 읽기)."""
        return set(self._active[stream_type])

    def get_pending(self, stream_type: StreamingType) -> Set[str]:
        """desired - active: 구독해야 하지만 아직 활성화되지 않은 종목 집합."""
        return self._desired[stream_type] - self._active[stream_type]

    def is_active(self, code: str, stream_type: StreamingType) -> bool:
        """해당 종목이 현재 활성 구독 중인지 확인한다."""
        return code in self._active[stream_type]

    # ── 편의 메서드 ──────────────────────────────────────────────────

    def get_status(self) -> dict:
        """현재 구독 상태 요약 반환 (모니터링/디버깅용)."""
        return {
            stream_type.value: {
                "desired": sorted(self._desired[stream_type]),
                "active": sorted(self._active[stream_type]),
                "pending": sorted(self.get_pending(stream_type)),
            }
            for stream_type in StreamingType
        }
