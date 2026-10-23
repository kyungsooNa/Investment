"""코스피/코스닥 투자자 순매수 시간차트 표본 수집 태스크."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class IndexFlowSamplerTask(SchedulableTask):
    """장중(영업일)에만 누적 투자자 순매수를 주기적으로 표본으로 남긴다."""

    CHECK_INTERVAL_SEC = 300

    def __init__(self, *, index_flow_history_service, market_clock,
                 market_calendar_service=None, check_interval_sec: Optional[int] = None,
                 logger=None) -> None:
        self._history = index_flow_history_service
        self._market_clock = market_clock
        self._mcs = market_calendar_service
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None

    @property
    def task_name(self) -> str:
        return "index_flow_sampler"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> dict:
        return {"running": self._state == TaskState.RUNNING}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    self._state = TaskState.RUNNING
                    await self._tick()
                    if self._state == TaskState.RUNNING:
                        self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error("%s: loop error — %s", self.task_name, exc, exc_info=True)
            await asyncio.sleep(self._check_interval_sec)

    async def _tick(self) -> None:
        if not await self._is_market_open_now():
            return
        await self._history.record_sample()

    async def _is_market_open_now(self) -> bool:
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._mcs is None:
            return True
        return await self._mcs.is_business_day(now.strftime("%Y%m%d"))
