"""Daily market timing update task."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Dict, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class MarketTimingDailyUpdateTask(SchedulableTask):
    """Emits the daily KOSPI/KOSDAQ market timing event outside strategy scans."""

    CHECK_INTERVAL_SEC = 60
    PRE_OPEN_WINDOW_MIN = 30

    def __init__(
        self,
        *,
        universe_service,
        market_clock,
        market_calendar_service=None,
        check_interval_sec: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._universe_service = universe_service
        self._market_clock = market_clock
        self._mcs = market_calendar_service
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._last_checked_date: Optional[str] = None
        self._last_result: Dict = {"ok": True}

    @property
    def task_name(self) -> str:
        return "market_timing_daily_update"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "last_checked_date": self._last_checked_date,
            "last_result": self._last_result,
        }

    async def start(self) -> None:
        if self._task is None or self._task.done():
            if self._state == TaskState.STOPPED:
                self._state = TaskState.IDLE
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
                await asyncio.sleep(self._check_interval_sec)
                if self._state == TaskState.SUSPENDED:
                    continue
                if await self._should_run_now():
                    self._state = TaskState.RUNNING
                    try:
                        await self.run_once()
                    finally:
                        if self._state == TaskState.RUNNING:
                            self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error("%s: loop error - %s", self.task_name, exc, exc_info=True)

    async def _check_business_day(self, date_key: str) -> bool:
        """거래일 판정. 해외 서브클래스가 동기 캘린더로 재정의한다."""
        return await self._mcs.is_business_day(date_key)

    async def _should_run_now(self) -> bool:
        if not self._market_clock:
            return False
        now = self._market_clock.get_current_kst_time()
        date_key = now.strftime("%Y%m%d")
        if self._last_checked_date == date_key:
            return False
        if self._mcs is not None:
            try:
                if not await self._check_business_day(date_key):
                    return False
            except Exception as exc:
                self._logger.warning("%s: business day check failed - %s", self.task_name, exc)
                return False
        open_time = self._market_clock.get_market_open_time()
        return open_time - timedelta(minutes=self.PRE_OPEN_WINDOW_MIN) <= now < open_time

    async def run_once(self) -> Dict:
        now = self._market_clock.get_current_kst_time()
        date_key = now.strftime("%Y%m%d")
        if self._mcs is not None:
            try:
                if not await self._check_business_day(date_key):
                    self._last_result = {"ok": True, "skipped": "non_business_day", "date": date_key}
                    return self._last_result
            except Exception as exc:
                self._last_result = {"ok": False, "error": str(exc), "date": date_key}
                self._logger.warning("%s: business day check failed - %s", self.task_name, exc)
                return self._last_result
        markets = await self._universe_service.refresh_market_timing(
            caller=self.task_name,
            logger=self._logger,
        )
        self._last_checked_date = date_key
        self._last_result = {"ok": True, "date": date_key, "markets": markets}
        return self._last_result
