# scheduler/ticket_queue/ticket.py
"""
Ticket — 작업 요청 DTO.

payload는 JSON-serializable 타입(str/int/float/list/dict)만 허용한다.
함수나 객체 참조를 담으면 안 된다 (추후 Redis 직렬화 대비).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


POISON_PRIORITY: int = -1  # Graceful Shutdown용 독약 티켓 우선순위


@dataclass(order=False)
class Ticket:
    """WorkerPool에 전달되는 작업 요청 단위."""

    priority: int
    task_name: str
    payload: dict
    attempt: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __lt__(self, other: "Ticket") -> bool:
        """PriorityQueue 비교: 낮은 숫자 = 높은 우선순위."""
        return self.priority < other.priority

    def __le__(self, other: "Ticket") -> bool:
        return self.priority <= other.priority

    def is_poison(self) -> bool:
        return self.task_name == "__POISON__"

    @classmethod
    def poison(cls) -> "Ticket":
        """Graceful Shutdown용 독약 티켓 생성."""
        return cls(priority=POISON_PRIORITY, task_name="__POISON__", payload={})
