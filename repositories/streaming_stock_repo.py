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
from enum import Enum
from typing import Dict, Optional, Set


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

    def __init__(self, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._desired: Dict[StreamingType, Set[str]] = {t: set() for t in StreamingType}
        self._active: Dict[StreamingType, Set[str]] = {t: set() for t in StreamingType}
        self._lock = asyncio.Lock()

        # PT SQLite 영속화용 (ProgramTradingStreamService의 pt_subscriptions 테이블 재사용)
        self._db_conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        self._db_path: Optional[str] = None

    # ── 초기화 / 영속성 ──────────────────────────────────────────────

    def load_pt_desired_from_db(self, db_path: str) -> None:
        """앱 시작 시 SQLite pt_subscriptions 테이블에서 PT desired 상태를 복원."""
        self._db_path = db_path
        try:
            self._db_conn = sqlite3.connect(db_path, check_same_thread=False)
            with self._db_lock:
                cursor = self._db_conn.execute("SELECT code FROM pt_subscriptions")
                codes = {row[0] for row in cursor.fetchall()}
            # desired 초기화 (lock 불필요 — 앱 시작 단계, 단일 스레드)
            self._desired[StreamingType.PROGRAM_TRADING] = codes
            if codes:
                self._logger.info(f"StreamingStockRepo: PT desired 복원 {sorted(codes)}")
        except Exception as e:
            self._logger.warning(f"StreamingStockRepo: PT desired DB 복원 실패 (무시): {e}")

    def _persist_pt_desired(self, code: str, add: bool) -> None:
        """PT desired 상태를 SQLite에 동기 저장. add=True이면 INSERT, False이면 DELETE."""
        if self._db_conn is None:
            return
        try:
            with self._db_lock:
                if add:
                    self._db_conn.execute(
                        "INSERT OR IGNORE INTO pt_subscriptions (code) VALUES (?)", (code,)
                    )
                else:
                    self._db_conn.execute(
                        "DELETE FROM pt_subscriptions WHERE code = ?", (code,)
                    )
                self._db_conn.commit()
        except Exception as e:
            self._logger.warning(f"StreamingStockRepo: PT desired DB 업데이트 실패 ({code}): {e}")

    # ── 상태 변경 (asyncio.Lock 보호) ────────────────────────────────

    async def mark_desired(self, code: str, stream_type: StreamingType) -> None:
        """종목을 구독 대상으로 등록한다."""
        async with self._lock:
            self._desired[stream_type].add(code)
        if stream_type == StreamingType.PROGRAM_TRADING:
            self._persist_pt_desired(code, add=True)

    async def unmark_desired(self, code: str, stream_type: StreamingType) -> None:
        """종목을 구독 대상에서 제거한다."""
        async with self._lock:
            self._desired[stream_type].discard(code)
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
