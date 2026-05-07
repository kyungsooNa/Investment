"""
NotificationQueueTask — 알림 외부 핸들러(Telegram 등) 큐 소비 태스크.

NotificationService.emit()이 enqueue한 이벤트를 저우선순위 백그라운드에서
순차적으로 소비하여 외부 핸들러에 전달한다.

idle 감지 전략:
  Layer 1 — ForegroundScheduler가 포그라운드 작업 시작 시 BackgroundScheduler.suspend_all()을
             호출하므로, 이 태스크도 자동으로 SUSPENDED 상태로 전환된다.
  Layer 2 — asyncio cooperative scheduling: asyncio.wait_for(timeout=1.0) 대기 중 다른
             코루틴이 실행되고, 이벤트 처리 후 asyncio.sleep(poll_interval)로 추가 양보한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState

if TYPE_CHECKING:
    from services.notification_service import NotificationService


class NotificationQueueTask(SchedulableTask):
    """NotificationService의 외부 핸들러 큐를 소비하는 상시 동작 태스크."""

    def __init__(
        self,
        notification_service: "NotificationService",
        poll_interval: float = 1.0,
        telegram_config=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._ns = notification_service
        self._poll_interval = poll_interval
        self._telegram_config = telegram_config
        self._logger = logger or logging.getLogger(__name__)
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._resume_event: Optional[asyncio.Event] = None

    # ── SchedulableTask interface ─────────────────────────────────

    @property
    def task_name(self) -> str:
        return "notification_queue"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # 초기 상태는 RUNNING이므로 set
        self._state = TaskState.RUNNING
        self._tasks.append(asyncio.create_task(self._drain_loop()))
        self._logger.info("NotificationQueueTask 시작")

    async def stop(self) -> None:
        self._logger.info(f"NotificationQueueTask 종료 시작: {len(self._tasks)}개 태스크")
        # SUSPENDED 상태라면 drain_loop이 event.wait()에서 블로킹 중이므로 set() 후 cancel
        if self._resume_event is not None:
            self._resume_event.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._logger.info("NotificationQueueTask 종료 완료")

    async def suspend(self) -> None:
        """일시 중지: resume_event를 clear하여 drain_loop을 블로킹. 큐에는 이벤트가 계속 쌓임."""
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED
            if self._resume_event is not None:
                self._resume_event.clear()
            self._logger.info("NotificationQueueTask 일시 중지 (큐 누적 중)")

    async def resume(self) -> None:
        """재개: resume_event를 set하여 drain_loop 블로킹 해제."""
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING
            if self._resume_event is not None:
                self._resume_event.set()
            self._logger.info("NotificationQueueTask 재개")

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "queued_events": self._ns.external_handler_queue.qsize(),
        }

    # ── Drain loop ────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """큐에서 이벤트를 하나씩 꺼내 모든 외부 핸들러에 순차 전달한다."""
        queue = self._ns.external_handler_queue
        while True:
            try:
                # SUSPENDED 상태에서는 event.wait()로 블로킹 (spin 없음)
                await self._resume_event.wait()

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if not self._should_send_external(event):
                    queue.task_done()
                    await asyncio.sleep(self._poll_interval)
                    continue

                for handler in self._ns.external_handlers:
                    try:
                        await handler(event)
                    except Exception as e:
                        self._logger.error(
                            f"[NotificationQueueTask] 핸들러 오류 ({getattr(handler, '__name__', handler)}): {e}"
                        )

                queue.task_done()
                await asyncio.sleep(self._poll_interval)

            except asyncio.CancelledError:
                self._logger.info("NotificationQueueTask drain_loop 취소됨")
                break
            except Exception as e:
                self._logger.error(f"[NotificationQueueTask] drain_loop 예외: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    def _should_send_external(self, event) -> bool:
        cfg = self._telegram_config
        if cfg is None:
            return True
        if not getattr(cfg, "enabled", True):
            return False
        metadata = getattr(event, "metadata", {}) or {}
        if isinstance(metadata, dict) and metadata.get("force_external"):
            return True
        route_levels = getattr(cfg, "route_levels", None)
        if not route_levels:
            return True
        category = getattr(event.category, "value", str(event.category))
        level = getattr(event.level, "value", str(event.level))
        allowed = route_levels.get(category) or route_levels.get(category.upper()) or []
        return level in allowed
