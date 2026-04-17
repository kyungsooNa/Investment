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
import gzip
import shutil
from typing import Dict, Optional, TYPE_CHECKING

from core.loggers.log_config import LOG_DELETE_DAYS, LOG_COMPRESS_DAYS
from task.background.after_market.after_market_task_base import AfterMarketTask
from interfaces.schedulable_task import TaskPriority

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class LogCleanupTask(AfterMarketTask):
    """오래된 로그 파일을 주기적으로 삭제하는 유지보수 태스크."""

    def __init__(
        self,
        log_dir: str = "logs",
        delete_days: int = LOG_DELETE_DAYS,
        compress_days: int = LOG_COMPRESS_DAYS,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        worker_pool=None,
    ) -> None:
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._log_dir = log_dir
        self._delete_days = delete_days
        self._compress_days = compress_days
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
        delete_cutoff = now - (self._delete_days * 86400)
        compress_cutoff = now - (self._compress_days * 86400)
        removed = 0
        compressed = 0
        errors = 0

        self._logger.info(
            f"LogCleanupTask 정리 시작 (기준일: {trading_date}, "
            f"보존 기간: {self._delete_days}일, 압축 기준: {self._compress_days}일, 경로: {self._log_dir})"
        )

        for root, _, files in os.walk(self._log_dir):
            for filename in files:
                if not (filename.endswith(".log") or filename.endswith(".json") or filename.endswith(".gz")):
                    continue
                file_path = os.path.join(root, filename)
                is_compressed = filename.endswith(".gz")

                try:
                    file_mtime = os.path.getmtime(file_path)
                    if file_mtime < delete_cutoff:
                        os.remove(file_path)
                        removed += 1
                        self._logger.debug(f"LogCleanupTask: 삭제 — {file_path}")
                    elif file_mtime < compress_cutoff and not is_compressed:
                        gz_file_path = file_path + ".gz"
                        with open(file_path, 'rb') as f_in:
                            with gzip.open(gz_file_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        os.remove(file_path)
                        compressed += 1
                        self._logger.debug(f"LogCleanupTask: 압축 — {file_path} -> .gz")
                    else:
                        self._logger.debug(f"LogCleanupTask: 스킵 — {file_path}")
                except Exception as e:
                    self._logger.warning(f"LogCleanupTask: 삭제 실패 — {file_path}: {e}")
                    errors += 1

        self._last_cleaned_date = trading_date
        self._progress = {"running": False}
        self._logger.info(
            f"LogCleanupTask 정리 완료 (기준일: {trading_date}, "
            f"압축: {compressed}개, 삭제: {removed}개, 실패: {errors}개)"
        )
