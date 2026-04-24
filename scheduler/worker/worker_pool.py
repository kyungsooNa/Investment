# scheduler/worker/worker_pool.py
"""
WorkerPool — N개 코루틴 워커가 MessageBroker에서 티켓을 소비한다.

- Retry: 실패 시 attempt += 1, Backoff 후 재발행 (최대 MAX_RETRIES회)
- DLQ: MAX_RETRIES 초과 시 DlqManager에 위임
- Suspend/Resume: asyncio.Event 기반 (ForegroundScheduler 연동)
- Graceful Shutdown: Poison Pill(priority=-1) N개 발행 후 join()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, List, Optional

from scheduler.ticket_queue.ticket import Ticket, POISON_PRIORITY
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.ticket_queue.dlq_manager import DlqManager
from core.loggers.trace_context import trace_scope


Handler = Callable[[dict], Awaitable[None]]


class WorkerPool:
    MAX_RETRIES: int = 3
    BASE_DELAY: float = 10.0  # seconds

    def __init__(
        self,
        broker: MessageBroker,
        dlq_manager: DlqManager,
        logger: Optional[logging.Logger] = None,
        num_workers: int = 2,
    ) -> None:
        self._broker = broker
        self._dlq = dlq_manager
        self._logger = logger or logging.getLogger(__name__)
        self._num_workers = num_workers
        self._registry: Dict[str, Handler] = {}
        self._worker_tasks: List[asyncio.Task] = []
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # 초기엔 통과 상태

    def register(self, task_name: str, handler: Handler) -> None:
        """task_name에 대응하는 핸들러(bound method 권장)를 등록한다."""
        self._registry[task_name] = handler
        self._logger.info(f"[WorkerPool] 핸들러 등록: {task_name}")

    def unregister(self, task_name: str) -> None:
        self._registry.pop(task_name, None)

    async def start(self) -> None:
        """N개의 워커 루프 코루틴을 asyncio.Task로 시작한다."""
        if self._worker_tasks:
            return
        for i in range(self._num_workers):
            t = asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            self._worker_tasks.append(t)
        self._logger.info(f"[WorkerPool] 워커 {self._num_workers}개 시작")

    async def shutdown(self) -> None:
        """Poison Pill(priority=-1)을 발행하고 모든 워커 Task가 종료될 때까지 대기한다.

        broker.join()을 사용하지 않는 이유:
        큐에 LOW 티켓이 남아 있을 때 Poison Pill이 먼저 처리되어 워커가 종료되면
        나머지 티켓의 task_done()이 호출되지 않아 join()이 영원히 대기하기 때문.
        대신 asyncio.gather()로 worker asyncio.Task 완료를 기다린다.
        """
        self._logger.info("[WorkerPool] Graceful Shutdown 시작")
        self._resume_event.set()  # suspend 중이어도 독약 수신 위해 해제
        for _ in range(self._num_workers):
            await self._broker.publish(Ticket.poison())
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._logger.info("[WorkerPool] 모든 워커 종료 완료")
        self._worker_tasks.clear()

    def suspend(self) -> None:
        """다음 티켓 소비 전에 워커를 일시 정지한다 (ForegroundScheduler 연동)."""
        self._resume_event.clear()
        self._logger.info("[WorkerPool] 일시 정지")

    def resume(self) -> None:
        """일시 정지된 워커를 재개한다."""
        self._resume_event.set()
        self._logger.info("[WorkerPool] 재개")

    @property
    def is_suspended(self) -> bool:
        return not self._resume_event.is_set()

    async def _worker_loop(self, worker_id: int) -> None:
        self._logger.info(f"[Worker-{worker_id}] 시작")
        while True:
            await self._resume_event.wait()  # suspend 시 여기서 블로킹
            ticket = await self._broker.consume()
            try:
                if ticket.is_poison():
                    self._logger.info(f"[Worker-{worker_id}] 독약 수신 → 종료")
                    break
                await self._handle(worker_id, ticket)
            finally:
                self._broker.task_done()
        self._logger.info(f"[Worker-{worker_id}] 종료")

    async def _handle(self, worker_id: int, ticket: Ticket) -> None:
        handler = self._registry.get(ticket.task_name)
        if not handler:
            self._logger.warning(f"[Worker-{worker_id}] 등록되지 않은 태스크: {ticket.task_name}")
            return
        try:
            with trace_scope(ticket.trace_id):
                await handler(ticket.payload)
        except Exception as e:
            with trace_scope(ticket.trace_id):
                self._logger.error(
                    f"[Worker-{worker_id}] 작업 실패: {ticket.task_name} (시도 {ticket.attempt + 1}/{self.MAX_RETRIES}) — {e}",
                    exc_info=True,
                )
            ticket.attempt += 1
            if ticket.attempt < self.MAX_RETRIES:
                delay = self.BASE_DELAY * ticket.attempt
                self._logger.info(f"[Worker-{worker_id}] {delay:.0f}s 후 재시도: {ticket.task_name}")
                await asyncio.sleep(delay)
                await self._broker.publish(ticket)
            else:
                await self._dlq.handle_failed_ticket(ticket, str(e))
