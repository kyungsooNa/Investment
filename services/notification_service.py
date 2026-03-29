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

    def __init__(self, market_clock: MarketClock):
        self._market_clock = market_clock
        self._history: List[NotificationEvent] = []
        self._subscriber_queues: List[asyncio.Queue] = []
        self._external_handlers: List[Callable[..., Coroutine[Any, Any, None]]] = []

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

        for handler in self._external_handlers:
            asyncio.create_task(handler(event))

        return event

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

    # ── 외부 핸들러 등록 (Telegram/Slack 등) ──

    def register_external_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ):
        self._external_handlers.append(handler)
