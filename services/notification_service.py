"""중앙 집중형 알림 이벤트 관리자.

모든 시스템 이벤트(매매 시그널, API 응답, 오류 등)를 수집하고
SSE 구독자에게 실시간 전파한다.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable, Coroutine, Any, Dict, List, Optional
from enum import Enum

from core.market_clock import MarketClock


class NotificationCategory(str, Enum):
    STRATEGY = "STRATEGY"
    BACKGROUND = "BACKGROUND"
    TRADE = "TRADE"
    API = "API"
    SYSTEM = "SYSTEM"

class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class NotificationEvent:
    """알림 이벤트."""
    id: str
    timestamp: str          # ISO format (KST)
    category: NotificationCategory
    level: NotificationLevel
    title: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class NotificationService:
    """시스템 전체 알림 이벤트 허브.

    사용법:
        nm = NotificationService(market_clock)
        await nm.emit(NotificationCategory.TRADE, NotificationCategory.CIRITICAL, "매수 시그널", "삼성전자 72,000원")
    """

    MAX_HISTORY = 200
    MAX_EXTERNAL_QUEUE_SIZE = 500
    _EXT_DEDUP_WINDOW_SEC = 5  # 외부 핸들러 2차 dedup 윈도우 (초)

    def __init__(self, market_clock: MarketClock):
        self._market_clock = market_clock
        self._history: List[NotificationEvent] = []
        self._subscriber_queues: List[asyncio.Queue] = []
        self._external_handlers: List[Callable[..., Coroutine[Any, Any, None]]] = []
        self._external_handler_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_EXTERNAL_QUEUE_SIZE)
        # 외부 핸들러 2차 dedup: (dedup_key, severity) → 마지막 emit 단조 시각
        self._ext_dedup_seen: Dict[tuple, float] = {}

    # ── 이벤트 발행 ──

    async def emit(
        self,
        category: NotificationCategory,
        level: NotificationLevel,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationEvent:
        """이벤트 생성 → 히스토리 저장 → 구독자 전파."""
        event = NotificationEvent(
            id=uuid.uuid4().hex[:12],
            timestamp=self._market_clock.get_current_kst_time().isoformat(),
            category=category,
            level=level,
            title=title,
            message=message,
            metadata=metadata or {},
        )
        
        self._history.append(event)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

        json_data = json.dumps(event.to_dict(), ensure_ascii=False)
        for queue in list(self._subscriber_queues):
            try:
                queue.put_nowait(json_data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(json_data)
                except Exception:
                    pass

        if self._external_handlers and not self._ext_dedup_blocked(event):
            try:
                self._external_handler_queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    self._external_handler_queue.get_nowait()
                    self._external_handler_queue.put_nowait(event)
                except Exception:
                    pass

        return event

    def _ext_dedup_blocked(self, event: "NotificationEvent") -> bool:
        """외부 핸들러 2차 dedup: OperatorAlertService 경유 이벤트의 중복 전파를 차단.

        metadata에 dedup_key와 transition이 모두 없으면 통과(기존 이벤트 영향 없음).
        dedup_key가 있고 같은 severity가 윈도우 내 이미 전파됐으면 차단.
        """
        import time as _time
        meta = event.metadata or {}
        dedup_key = meta.get("dedup_key")
        if not dedup_key:
            return False  # OperatorAlertService 경유 이벤트가 아님 → 통과
        severity = event.level.value
        cache_key = (dedup_key, severity)
        now = _time.monotonic()
        last = self._ext_dedup_seen.get(cache_key, 0.0)
        if (now - last) < self._EXT_DEDUP_WINDOW_SEC:
            return True  # 차단
        self._ext_dedup_seen[cache_key] = now
        # 오래된 항목 정리 (메모리 leak 방지)
        stale = [k for k, v in self._ext_dedup_seen.items() if (now - v) > 3600]
        for k in stale:
            self._ext_dedup_seen.pop(k, None)
        return False

    # ── SSE 구독자 관리 ──

    def create_subscriber_queue(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscriber_queues.append(queue)
        return queue

    def remove_subscriber_queue(self, queue: asyncio.Queue):
        if queue in self._subscriber_queues:
            self._subscriber_queues.remove(queue)

    # ── 최근 이벤트 조회 ──

    def get_recent(
        self, count: int = 50, category: Optional[NotificationCategory] = None
    ) -> List[dict]:
        items = self._history
        if category:
            items = [e for e in items if e.category == category]
        return [e.to_dict() for e in items[-count:]][::-1]

    # ── 외부 핸들러 등록 (Telegram) ──

    def register_external_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ):
        self._external_handlers.append(handler)

    @property
    def external_handler_queue(self) -> asyncio.Queue:
        """외부 핸들러 전달 대기 큐 (NotificationQueueTask가 소비)."""
        return self._external_handler_queue

    @property
    def external_handlers(self):
        """등록된 외부 핸들러 목록 (방어적 복사)."""
        return list(self._external_handlers)
