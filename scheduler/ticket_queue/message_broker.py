# scheduler/ticket_queue/message_broker.py
"""
MessageBroker — asyncio.PriorityQueue 래퍼.

- Backpressure: maxsize 초과 시 QueueFull 대신 경고 로깅 후 스킵
- 추후 Redis Streams 교체 지점 (publish/consume 인터페이스 유지)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from scheduler.ticket_queue.ticket import Ticket


class MessageBroker:
    def __init__(self, maxsize: int = 100, logger: Optional[logging.Logger] = None) -> None:
        self._queue: asyncio.PriorityQueue[Ticket] = asyncio.PriorityQueue(maxsize=maxsize)
        self._logger = logger or logging.getLogger(__name__)

    async def publish(self, ticket: Ticket) -> bool:
        """티켓을 큐에 삽입한다. 큐가 가득 찬 경우 경고 로그 후 False 반환."""
        try:
            self._queue.put_nowait(ticket)
            return True
        except asyncio.QueueFull:
            self._logger.warning(
                f"[MessageBroker] 큐 포화 (maxsize={self._queue.maxsize}) — "
                f"티켓 드롭: {ticket.task_name}"
            )
            return False

    async def consume(self) -> Ticket:
        """우선순위가 가장 높은 티켓을 꺼낸다 (없으면 대기)."""
        return await self._queue.get()

    def task_done(self) -> None:
        """consume() 후 처리 완료를 알린다 (join() 대기 해제용)."""
        self._queue.task_done()

    async def join(self) -> None:
        """큐의 모든 아이템이 처리될 때까지 대기한다."""
        await self._queue.join()

    @property
    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
