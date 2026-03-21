# scheduler/foreground_scheduler.py
"""
포그라운드 태스크 스케줄러.
UserAction 실행 시 BackgroundScheduler의 태스크를 suspend/resume 조율한다.
Reference counting 방식: 첫 foreground action → suspend, 마지막 완료 → resume.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from scheduler.background_scheduler import BackgroundScheduler
from core.performance_profiler import PerformanceProfiler


class ForegroundScheduler:
    """UserAction과 백그라운드 태스크 간 우선순위 조율 스케줄러.

    foreground action이 실행되면 BackgroundScheduler의 태스크를 일시 중지하고,
    모든 foreground action이 완료되면 다시 재개한다.
    """

    def __init__(
        self,
        background_scheduler: BackgroundScheduler,
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
    ):
        self._bg = background_scheduler
        self._logger = logger or logging.getLogger(__name__)
        self._pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._active_count = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def context(self):
        """Foreground 우선순위 컨텍스트 매니저.

        첫 진입 시 BackgroundScheduler를 suspend하고,
        마지막 퇴장 시 resume한다. 미들웨어에서 사용.

        Usage::

            async with fg.context():
                # broker API 호출 등 foreground 작업
                ...
        """
        async with self._lock:
            self._active_count += 1
            if self._active_count == 1:
                self._logger.debug("[ForegroundScheduler] 백그라운드 태스크 일시 중지")
                await self._bg.suspend_all()
        try:
            yield
        finally:
            async with self._lock:
                self._active_count -= 1
                if self._active_count == 0:
                    self._logger.debug("[ForegroundScheduler] 백그라운드 태스크 재개")
                    await self._bg.resume_all()

    async def execute(self, coro):
        """포그라운드 태스크를 실행한다.

        첫 foreground action 시 BackgroundScheduler를 suspend하고,
        마지막 foreground action 완료 시 resume한다.

        Args:
            coro: 실행할 코루틴 (awaitable).

        Returns:
            코루틴의 반환값.
        """
        async with self.context():
            return await coro

    @property
    def active_count(self) -> int:
        """현재 실행 중인 foreground action 수."""
        return self._active_count

    @property
    def is_active(self) -> bool:
        """foreground action이 실행 중인지 여부."""
        return self._active_count > 0
