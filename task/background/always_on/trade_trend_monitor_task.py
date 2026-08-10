from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from repositories.trade_trend_repository import TradeTrendRepository
from services.trade_trend_service import (
    build_jeju_semiconductor_report,
    select_export_row,
)


def _month_delta(yyyymm: str, delta_months: int) -> str:
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6]) + delta_months
    while month <= 0:
        year -= 1
        month += 12
    while month > 12:
        year += 1
        month -= 12
    return f"{year:04d}{month:02d}"


def _latest_available_month(now: datetime) -> str:
    # 지역별 품목 월간 통계는 익월 중순 이후 확인하는 보수적 기준으로 본다.
    first_day_this_month = now.replace(day=1)
    return _month_delta(first_day_this_month.strftime("%Y%m"), -1)


class TradeTrendMonitorTask(SchedulableTask):
    def __init__(
        self,
        *,
        customs_client,
        national_client=None,
        repository_path: str = "data/trade_trend_state.json",
        telegram_reporter=None,
        config=None,
        market_clock=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._client = customs_client
        self._national_client = national_client
        self._repository = TradeTrendRepository(repository_path)
        self._reporter = telegram_reporter
        self._config = config
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._tasks: list[asyncio.Task] = []
        self._tick_lock: Optional[asyncio.Lock] = None
        self._progress = {
            "running": False,
            "last_checked_at": None,
            "last_success_at": None,
            "last_period": None,
            "sent_count": 0,
            "national_sent_count": 0,
            "last_error": None,
        }

    @property
    def task_name(self) -> str:
        return "trade_trend_monitor"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> dict:
        return dict(self._progress)

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        self._state = TaskState.RUNNING
        self._progress["running"] = True
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._progress["running"] = False

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING

    def _get_tick_lock(self) -> asyncio.Lock:
        if self._tick_lock is None:
            self._tick_lock = asyncio.Lock()
        return self._tick_lock

    async def _loop(self) -> None:
        try:
            while True:
                if self._state == TaskState.RUNNING:
                    try:
                        await self._tick()
                    except Exception as exc:
                        self._progress["last_error"] = f"{type(exc).__name__}: {exc}"
                        self._logger.error(
                            "%s: 수출입동향 조회 실패 — %s",
                            self.task_name,
                            exc,
                            exc_info=True,
                        )
                await asyncio.sleep(int(getattr(self._config, "poll_interval_sec", 3600)))
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        lock = self._get_tick_lock()
        if lock.locked():
            return
        async with lock:
            now = self._market_clock.get_current_kst_time()
            yyyymm = str(getattr(self._config, "target_month", "") or "")
            if not yyyymm:
                yyyymm = _latest_available_month(now)
            self._progress["last_checked_at"] = now.isoformat()
            self._progress["last_period"] = yyyymm
            self._progress["last_error"] = None

            if bool(getattr(self._config, "national_enabled", True)):
                await self._send_national_releases()

            if not bool(getattr(self._config, "jeju_semiconductor_enabled", True)):
                return
            if self._client is None:
                return

            item_code = str(getattr(self._config, "item_code", "8542"))
            previous_month = _month_delta(yyyymm, -1)
            previous_year = _month_delta(yyyymm, -12)

            current = select_export_row(
                await self._client.fetch_sido_item_month(yyyymm, item_code)
            )
            if current is None:
                return
            prev = select_export_row(
                await self._client.fetch_sido_item_month(previous_month, item_code)
            )
            prev_year = select_export_row(
                await self._client.fetch_sido_item_month(previous_year, item_code)
            )
            total = select_export_row(await self._client.fetch_sido_total_month(yyyymm))

            report = build_jeju_semiconductor_report(
                current=current,
                previous_month=prev,
                previous_year=prev_year,
                jeju_total=total,
                fetched_at=now,
            )
            if self._repository.has_sent(report.dedup_key):
                self._progress["last_success_at"] = now.isoformat()
                return
            if self._reporter is None:
                return

            sent = await self._reporter.send_jeju_semiconductor_trade_report(report)
            if sent:
                self._repository.mark_sent(report.dedup_key)
                self._progress["sent_count"] += 1
                self._progress["last_success_at"] = now.isoformat()

    async def _send_national_releases(self) -> None:
        if self._national_client is None or self._reporter is None:
            return
        releases = await self._national_client.fetch_recent_releases()
        for release in releases:
            if self._repository.has_sent(release.dedup_key):
                continue
            sent = await self._reporter.send_national_trade_trend_report(release)
            if sent:
                self._repository.mark_sent(release.dedup_key)
                self._progress["national_sent_count"] += 1
