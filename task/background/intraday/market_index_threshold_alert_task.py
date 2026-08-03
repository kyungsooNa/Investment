"""코스피/코스닥 지수 급변 사전경고 태스크."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class MarketIndexThresholdAlertTask(SchedulableTask):
    """1분봉 지수 시세로 ±5%, -8% 등락률을 감시한다."""

    CHECK_INTERVAL_SEC = 60
    _INDICES = (("0001", "코스피"), ("1001", "코스닥"))
    _DOWN_SIGNS = {"4", "5", "-", "D", "DOWN"}

    def __init__(self, *, broker, market_status_alert_service, market_clock,
                 market_calendar_service=None, check_interval_sec: Optional[int] = None,
                 logger=None) -> None:
        self._broker = broker
        self._alert_service = market_status_alert_service
        self._market_clock = market_clock
        self._mcs = market_calendar_service
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None

    @property
    def task_name(self) -> str:
        return "market_index_threshold_alert"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

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
        for index_code, index_name in self._INDICES:
            response = await self._broker.inquire_time_indexchartprice(index_code, 60)
            if response.rt_cd != ErrorCode.SUCCESS.value:
                self._logger.warning("%s: %s 지수 조회 실패 — %s", self.task_name, index_name, response.msg1)
                continue
            summary = (response.data or {}).get("summary") or {}
            change_rate = self._signed_change_rate(summary)
            if change_rate is None:
                self._logger.warning("%s: %s 등락률 누락", self.task_name, index_name)
                continue
            await self._alert_service.on_index_change(index_code, index_name, change_rate)

    async def _is_market_open_now(self) -> bool:
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._mcs is None:
            return True
        return await self._mcs.is_business_day(now.strftime("%Y%m%d"))

    @classmethod
    def _signed_change_rate(cls, summary: dict) -> Optional[float]:
        try:
            rate = float(str(summary.get("bstp_nmix_prdy_ctrt") or "").replace(",", ""))
        except (TypeError, ValueError):
            return None
        sign = str(summary.get("prdy_vrss_sign") or "").upper()
        return -abs(rate) if sign in cls._DOWN_SIGNS else rate
