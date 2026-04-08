# task/background/after_market/after_market_task_base.py
"""
AfterMarketTask — 장 마감 후 실행되는 배치 태스크의 공통 기반 클래스.

모든 after_market 태스크가 공유하는 보일러플레이트를 단일 위치에서 관리한다.

공통 제공
---------
- ``_state``, ``_tasks`` 필드 초기화
- ``state`` / ``priority`` property
- ``stop()``  — asyncio.Task 취소 및 정리
- ``suspend()`` / ``resume()`` — 기본(상태 전환만). 청크 중단이 필요한 서브클래스는 재정의.
- ``_after_market_scheduler()`` — ``run_after_market_loop`` 연결

서브클래스 필수 구현
--------------------
- ``task_name`` property
- ``_scheduler_label`` property — run_after_market_loop 레이블 (로그 식별자)
- ``_on_market_closed(latest_trading_date: str)`` — 장 마감 콜백
- ``start()`` — 태스크 시작 (초기 1회 실행 + 스케줄러 등록)
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, TYPE_CHECKING

import yaml

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from scheduler.after_market_loop import run_after_market_loop

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService

from pydantic import BaseModel, Field

class AfterMarketTasksConfig(BaseModel):
    after_market_delay_sec: Dict[str, int] = Field(default_factory=dict)

class TaskConfigModel(BaseModel):
    after_market_tasks: AfterMarketTasksConfig = Field(default_factory=AfterMarketTasksConfig)

_TASK_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "config", "task_config.yaml",
)
_DEFAULT_DELAYS: Dict[str, int] = {}

def _load_after_market_delays() -> Dict[str, int]:
    """task_config.yaml 에서 after_market_delay_sec 매핑을 로드한다."""
    global _DEFAULT_DELAYS
    if _DEFAULT_DELAYS:
        return _DEFAULT_DELAYS
    try:
        with open(_TASK_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        # Pydantic 모델을 통한 안전한 파싱, 타입 캐스팅 및 기본값 할당
        config = TaskConfigModel(**raw)
        _DEFAULT_DELAYS = {k: v * 60 for k, v in config.after_market_tasks.after_market_delay_sec.items()}
    except Exception:
        _DEFAULT_DELAYS = {}
    return _DEFAULT_DELAYS


class AfterMarketTask(SchedulableTask, ABC):
    """장 마감 후 주기적으로 실행되는 배치 태스크의 공통 기반 클래스."""

    def __init__(
        self,
        mcs: Optional["MarketCalendarService"],
        market_clock: Optional["MarketClock"],
        logger: Optional[logging.Logger],
    ) -> None:
        self._mcs = mcs
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(self.__class__.__module__)
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []

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

    # ── 장마감 후 스케줄러 ────────────────────────────────────────

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
            if self._state not in (TaskState.SUSPENDED, TaskState.STOPPED):
                self._state = TaskState.RUNNING
            try:
                await self._on_market_closed(date)
            finally:
                if self._state == TaskState.RUNNING:
                    self._state = TaskState.IDLE  # 작업 완료 → 대기 복귀

        delay_sec = _load_after_market_delays().get(self.task_name, 0)
        await run_after_market_loop(
            mcs=self._mcs,
            market_clock=self._market_clock,
            logger=self._logger,
            on_market_closed=_on_closed_with_state,
            label=self._scheduler_label,
            delay_sec=delay_sec,
        )

    @abstractmethod
    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백 — 서브클래스에서 구체적인 작업을 구현한다."""
