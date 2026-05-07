"""장 시작 직후 원장/브로커 잔고 대사 task."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from services.notification_service import NotificationCategory, NotificationLevel


class OpeningPositionReconcileTask(SchedulableTask):
    CHECK_INTERVAL_SEC = 30
    OPEN_DELAY_SEC = 60
    RUN_WINDOW_MIN = 10

    def __init__(
        self,
        *,
        reconcile_service,
        market_calendar_service=None,
        market_clock=None,
        notification_service=None,
        logger: Optional[logging.Logger] = None,
        check_interval_sec: Optional[int] = None,
        open_delay_sec: Optional[int] = None,
        run_window_min: Optional[int] = None,
    ) -> None:
        self._service = reconcile_service
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._ns = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._open_delay_sec = open_delay_sec if open_delay_sec is not None else self.OPEN_DELAY_SEC
        self._run_window_min = run_window_min if run_window_min is not None else self.RUN_WINDOW_MIN
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._last_checked_date: Optional[str] = None
        self._last_result: Dict = {"mismatch_count": None, "error": None}

    @property
    def task_name(self) -> str:
        return "opening_position_reconcile"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

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
                self._logger.error(f"[OpeningPositionReconcile] loop error: {exc}", exc_info=True)

    async def _should_run_now(self) -> bool:
        if not self._market_clock or not self._mcs:
            return False
        now = self._market_clock.get_current_kst_time()
        date_key = now.strftime("%Y%m%d")
        if self._last_checked_date == date_key:
            return False
        try:
            if not await self._mcs.is_business_day(date_key):
                return False
        except Exception:
            return False
        start_at = self._market_clock.get_market_open_time() + timedelta(seconds=self._open_delay_sec)
        end_at = start_at + timedelta(minutes=self._run_window_min)
        return start_at <= now < end_at

    async def run_once(self) -> Dict:
        try:
            result = await self._service.reconcile_once()
            self._last_result = result
            if self._market_clock:
                self._last_checked_date = self._market_clock.get_current_kst_time().strftime("%Y%m%d")

            mismatch_count = int(result.get("mismatch_count") or 0)
            if mismatch_count:
                self._logger.warning(f"[OpeningPositionReconcile] mismatch_count={mismatch_count} result={result}")
                if self._ns:
                    await self._ns.emit(
                        NotificationCategory.TRADE,
                        NotificationLevel.WARNING,
                        "장 시작 원장/잔고 대사 불일치",
                        f"{mismatch_count}건 확인",
                        metadata=result,
                    )
            else:
                self._logger.info("[OpeningPositionReconcile] OK")
            return result
        except Exception as exc:
            self._last_result = {"mismatch_count": None, "error": str(exc)}
            self._logger.error(f"[OpeningPositionReconcile] 실패: {exc}", exc_info=True)
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.TRADE,
                    NotificationLevel.ERROR,
                    "장 시작 원장/잔고 대사 실패",
                    str(exc),
                    metadata=self._last_result,
                )
            return self._last_result
