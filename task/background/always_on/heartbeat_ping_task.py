"""HeartbeatPingTask — 외부 생존 감시(dead-man switch) 서비스로 주기 ping 을 보낸다.

앱 내부 알림(텔레그램 등)은 프로세스가 살아 있어야 나간다. 프로세스 크래시·호스트 다운·
네트워크 단절이면 "죽었다" 는 알림을 보낼 주체가 없으므로, 외부 감시 서비스
(healthchecks.io 호환 ping URL 등)가 ping 이 끊긴 것을 보고 알리게 한다.
장중에만 감시하려면 감시 서비스 쪽 cron 스케줄로 지정한다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class HeartbeatPingTask(SchedulableTask):
    def __init__(
        self,
        *,
        url: str,
        interval_sec: float = 60.0,
        timeout_sec: float = 10.0,
        logger: Optional[logging.Logger] = None,
        client_factory: Optional[Callable[[float], httpx.AsyncClient]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._url = url
        self._interval_sec = interval_sec
        self._timeout_sec = timeout_sec
        self._logger = logger or logging.getLogger(__name__)
        self._client_factory = client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))
        self._sleep = sleep or asyncio.sleep
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._last_ok_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._consecutive_failures = 0

    @property
    def task_name(self) -> str:
        return "heartbeat"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._tasks.append(asyncio.create_task(self._loop()))
        self._logger.info(f"HeartbeatPingTask 시작 (interval={self._interval_sec}s)")

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        # 의도적으로 무시한다: 포그라운드 작업 중 ping 이 끊기면 외부 감시가 오탐 알림을 낸다.
        return

    async def resume(self) -> None:
        return

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "interval_sec": self._interval_sec,
            "last_ok_at": self._last_ok_at,
            "last_error": self._last_error,
            "consecutive_failures": self._consecutive_failures,
        }

    async def ping_once(self) -> bool:
        try:
            async with self._client_factory(self._timeout_sec) as client:
                response = await client.get(self._url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._consecutive_failures += 1
            # URL 자체가 비밀값(감시 ID)이므로 로그에 남기지 않는다.
            self._last_error = type(exc).__name__
            self._logger.warning(
                f"[HeartbeatPingTask] ping 실패 ({self._last_error}, 연속 {self._consecutive_failures}회)"
            )
            return False
        self._consecutive_failures = 0
        self._last_error = None
        self._last_ok_at = datetime.now().astimezone().isoformat()
        return True

    async def _loop(self) -> None:
        while True:
            try:
                await self.ping_once()
                await self._sleep(self._interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # 루프가 죽으면 감시가 오탐(사망)으로 보므로 계속 돈다
                self._logger.error(f"[HeartbeatPingTask] 루프 예외: {exc}", exc_info=True)
                await self._sleep(self._interval_sec)
