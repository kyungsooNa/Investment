"""Daily paper account expiry alert task."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class PaperAccountExpiryAlertTask(SchedulableTask):
    CHECK_INTERVAL_SEC = 60 * 60

    def __init__(
        self,
        *,
        alert_service,
        env,
        market_clock,
        logger: Optional[logging.Logger] = None,
        check_interval_sec: int = CHECK_INTERVAL_SEC,
    ) -> None:
        self._alert_service = alert_service
        self._env = env
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._check_interval_sec = int(check_interval_sec)
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._last_checked_date: Optional[str] = None
        self._last_result: Dict = {"alerted": False, "reason": "never_checked"}

    @property
    def task_name(self) -> str:
        return "paper_account_expiry_alert"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        if self._state == TaskState.STOPPED:
            self._state = TaskState.IDLE
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "last_checked_date": self._last_checked_date,
            "last_result": self._last_result,
        }

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
                self._logger.error(f"[PaperAccountExpiryAlert] loop error: {exc}", exc_info=True)

    async def _should_run_now(self) -> bool:
        if self._market_clock is None:
            return False
        date_key = self._market_clock.get_current_kst_time().strftime("%Y%m%d")
        return self._last_checked_date != date_key

    async def run_once(self) -> Dict:
        if self._market_clock is None:
            return {"alerted": False, "reason": "market_clock_missing"}
        date_key = self._market_clock.get_current_kst_time().strftime("%Y%m%d")
        if self._last_checked_date == date_key:
            return {"alerted": False, "reason": "already_checked_today"}

        is_paper = bool(getattr(self._env, "is_paper_trading", False))
        account = str(getattr(self._env, "paper_stock_account_number", "") or "")
        result = await self._alert_service.check_and_notify(is_paper, account)
        self._last_checked_date = date_key
        self._last_result = result
        return result
