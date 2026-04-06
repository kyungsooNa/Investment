# task/background/after_market/log_cleanup_task.py
"""
오래된 로그 파일을 정리하는 백그라운드 유지보수 태스크.

장 마감 5시간(300분) 후 logs/ 디렉토리를 순회하여 지정된 일수(기본 30일)보다
오래된 로그 파일(.log, .json)을 삭제한다.

Logger.__init__ 에서도 동일한 정리가 실행되지만, 앱이 장기 실행될 때
재시작 없이 주기적으로 로그를 관리하기 위해 이 태스크를 별도로 등록한다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskPriority, TaskState

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class LogCleanupTask(AfterMarketTask):
    """오래된 로그 파일을 주기적으로 삭제하는 유지보수 태스크."""

    def __init__(
        self,
        log_dir: str = "logs",
        days: int = 30,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
    ) -> None:
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
        )
        self._log_dir = log_dir
        self._days = days
        self._last_cleaned_date: Optional[str] = None
        self._progress: Dict = {"running": False}

    # ── SchedulableTask 인터페이스 ────────────────────────────────

    @property
    def task_name(self) -> str:
        return "log_cleanup"

    @property
    def _scheduler_label(self) -> str:
        return "LogCleanup"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.MAINTENANCE

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._tasks.append(asyncio.create_task(self._after_market_scheduler()))
        self._logger.info("LogCleanupTask 시작")

    def get_progress(self) -> Dict:
        return dict(self._progress)

    # ── 장 마감 콜백 ─────────────────────────────────────────────

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일에 아직 정리하지 않았으면 실행."""
        if self._last_cleaned_date == latest_trading_date:
            self._logger.info(
                f"LogCleanupTask: {latest_trading_date} 이미 정리 완료 — 스킵"
            )
            return
        await asyncio.to_thread(self._cleanup, latest_trading_date)

    # ── 핵심 정리 로직 ────────────────────────────────────────────

    def _cleanup(self, trading_date: str) -> None:
        """logs/ 디렉토리를 순회하며 오래된 로그 파일을 삭제한다."""
        self._progress = {"running": True}
        now = time.time()
        cutoff = now - (self._days * 86400)
        removed = 0
        errors = 0

        self._logger.info(
            f"LogCleanupTask 정리 시작 (기준일: {trading_date}, "
            f"보존 기간: {self._days}일, 경로: {self._log_dir})"
        )

        for root, _, files in os.walk(self._log_dir):
            for filename in files:
                if ".log" not in filename and ".json" not in filename:
                    continue
                file_path = os.path.join(root, filename)
                try:
                    if os.path.getmtime(file_path) < cutoff:
                        os.remove(file_path)
                        removed += 1
                        self._logger.debug(f"LogCleanupTask: 삭제 — {file_path}")
                except Exception as e:
                    self._logger.warning(f"LogCleanupTask: 삭제 실패 — {file_path}: {e}")
                    errors += 1

        self._last_cleaned_date = trading_date
        self._progress = {"running": False}
        self._logger.info(
            f"LogCleanupTask 정리 완료 (기준일: {trading_date}, "
            f"삭제: {removed}개, 실패: {errors}개)"
        )
