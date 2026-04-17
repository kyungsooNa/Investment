# task/background/after_market/after_market_task_base.py
"""
AfterMarketTask — 장 마감 후 실행되는 배치 태스크의 공통 기반 클래스.

모든 after_market 태스크가 공유하는 보일러플레이트를 단일 위치에서 관리한다.

공통 제공
---------
- ``_state``, ``_tasks`` 필드 초기화
- ``state`` / ``priority`` property
- ``start()`` — WorkerPool 주입 시 Ticket-driven 핸들러 등록, 미주입 시 after_market_loop 폴백
- ``stop()``  — asyncio.Task 취소 및 정리
- ``suspend()`` / ``resume()`` — 기본(상태 전환만). 청크 중단이 필요한 서브클래스는 재정의.
- ``_after_market_scheduler()`` — ``run_after_market_loop`` 연결

서브클래스 필수 구현
--------------------
- ``task_name`` property
- ``_scheduler_label`` property — run_after_market_loop 레이블 (로그 식별자)
- ``_on_market_closed(latest_trading_date: str)`` — 장 마감 콜백

선택적 재정의
-------------
- ``_on_start_hook()`` — start() 전 초기화 (_suspend_event.set() 등)
- ``execute(payload: dict)`` — Ticket-driven 커스텀 처리 (기본: _on_market_closed 위임)
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional, TYPE_CHECKING

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from scheduler.after_market_loop import run_after_market_loop

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from scheduler.worker.worker_pool import WorkerPool


class AfterMarketTask(SchedulableTask, ABC):
    """장 마감 후 주기적으로 실행되는 배치 태스크의 공통 기반 클래스."""

    def __init__(
        self,
        mcs: Optional["MarketCalendarService"],
        market_clock: Optional["MarketClock"],
        logger: Optional[logging.Logger],
        worker_pool: Optional["WorkerPool"] = None,
    ) -> None:
        self._mcs = mcs
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(self.__class__.__module__)
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._running_depth: int = 0  # 중첩 _running_state() 호출 횟수
        self._worker_pool = worker_pool

    # ── SchedulableTask 공통 구현 ────────────────────────────────

    @property
    def state(self) -> TaskState:
        return self._state

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    async def stop(self) -> None:
        self._logger.info(f"{self.task_name} 종료 시작: {len(self._tasks)}개 태스크")
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._logger.info(f"{self.task_name} 종료 완료")

    async def suspend(self) -> None:
        """기본 구현: 상태만 전환. 청크 중단이 필요한 태스크는 재정의."""
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        """기본 구현: 상태만 전환. 청크 중단이 필요한 태스크는 재정의."""
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING

    async def start(self) -> None:
        """태스크 시작.

        WorkerPool이 주입된 경우: execute()를 handler로 등록 (Ticket-driven).
        주입되지 않은 경우: 기존 after_market_loop 방식으로 폴백.
        """
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        await self._on_start_hook()
        if self._worker_pool is not None:
            self._worker_pool.register(self.task_name, self.execute)
            self._logger.info(f"{self.task_name} WorkerPool 핸들러 등록 (Ticket-driven)")
        else:
            self._tasks.append(asyncio.create_task(self._after_market_scheduler()))
            self._logger.info(f"{self.task_name} 시작")

    async def _on_start_hook(self) -> None:
        """start() 전 서브클래스 초기화 훅 (예: _suspend_event.set()). 재정의 가능."""

    # ── 장마감 후 스케줄러 ────────────────────────────────────────

    @asynccontextmanager
    async def _running_state(self):
        """작업 실행 구간을 RUNNING으로 표시하는 컨텍스트 매니저.

        중첩 호출을 지원한다 (_running_depth 카운터). 가장 바깥 컨텍스트가
        종료될 때만 IDLE로 복귀하므로, force 메서드와 스케줄러 콜백이
        중첩되어도 상태가 조기에 IDLE로 바뀌지 않는다.
        SUSPENDED / STOPPED 상태는 덮어쓰지 않는다.
        """
        entered = self._state not in (TaskState.SUSPENDED, TaskState.STOPPED)
        if entered:
            self._running_depth += 1
            self._state = TaskState.RUNNING
        try:
            yield
        finally:
            if entered:
                self._running_depth -= 1
                if self._running_depth == 0 and self._state == TaskState.RUNNING:
                    self._state = TaskState.IDLE

    @property
    @abstractmethod
    def _scheduler_label(self) -> str:
        """run_after_market_loop 에 전달할 레이블 (로그 식별자)."""

    async def _after_market_scheduler(self) -> None:
        """장 마감 후 자동으로 작업을 스케줄링하는 루프."""
        # 루프 진입 = 대기 구간 시작 → IDLE
        if self._state not in (TaskState.SUSPENDED, TaskState.STOPPED):
            self._state = TaskState.IDLE

        async def _on_closed_with_state(date: str) -> None:
            async with self._running_state():
                await self._on_market_closed(date)

        try:
            await run_after_market_loop(
                mcs=self._mcs,
                market_clock=self._market_clock,
                logger=self._logger,
                on_market_closed=_on_closed_with_state,
                label=self._scheduler_label,
            )
        except asyncio.CancelledError:
            # Propagate cancellation so callers can cancel the task normally.
            raise
        except Exception as e:
            # Guard against unexpected errors (including test-mocking side-effects)
            # to avoid unraisable exceptions from background scheduler coroutines.
            try:
                self._logger.error(f"{self.task_name} 스케줄러 예외로 종료: {e}", exc_info=True)
            except Exception:
                # Ensure logging failures do not raise further
                pass

    @abstractmethod
    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백 — 서브클래스에서 구체적인 작업을 구현한다."""

    async def execute(self, payload: dict) -> None:
        """WorkerPool 핸들러 진입점.

        Ticket-driven 아키텍처로 전환된 서브클래스는 이 메서드를 재정의한다.
        기본 구현은 payload의 "date" 필드를 _on_market_closed()로 위임하여
        아직 전환되지 않은 태스크도 WorkerPool에서 호출 가능하도록 한다.
        """
        date = payload.get("date", "")
        async with self._running_state():
            await self._on_market_closed(date)

    async def force_run(self) -> None:
        """skip 조건을 무시하고 즉시 실행한다. 서브클래스에서 재정의한다."""
        raise NotImplementedError(f"{self.task_name}.force_run() 미구현")
