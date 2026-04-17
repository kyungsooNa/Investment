# scheduler/dispatcher/time_dispatcher.py
"""
TimeDispatcher — MarketClock을 1분마다 폴링하여 장 마감 시 Ticket을 발행한다.

- 실제 영업일(MarketCalendarService.get_latest_trading_date)로 날짜 검증
  → 주말/공휴일에는 티켓 미발행
- 동일 거래일에는 1회만 발행 (last_dispatched_date 추적)
- Graceful Stop: stop() 호출 시 폴링 루프 종료
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, TYPE_CHECKING

from interfaces.schedulable_task import TaskPriority
from scheduler.ticket_queue.ticket import Ticket
from scheduler.ticket_queue.message_broker import MessageBroker

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class TimeDispatcher:
    """장 마감을 감지하고 등록된 태스크 티켓을 MessageBroker에 발행한다."""

    POLL_INTERVAL: int = 60  # seconds

    def __init__(
        self,
        broker: MessageBroker,
        market_clock: "MarketClock",
        mcs: "MarketCalendarService",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._market_clock = market_clock
        self._mcs = mcs
        self._logger = logger or logging.getLogger(__name__)
        # task_name → priority 매핑
        self._task_schedule: Dict[str, int] = {}
        self._running = False
        self._sleep_task: Optional[asyncio.Task] = None

    def register_task(self, task_name: str, priority: int = TaskPriority.LOW) -> None:
        """장 마감 시 발행할 태스크를 등록한다."""
        self._task_schedule[task_name] = priority
        self._logger.info(f"[TimeDispatcher] 태스크 등록: {task_name} (priority={priority})")

    def unregister_task(self, task_name: str) -> None:
        self._task_schedule.pop(task_name, None)

    async def run(self) -> None:
        """장 마감 감지 폴링 루프. BackgroundScheduler가 Task로 실행한다."""
        self._running = True
        last_dispatched_date: Optional[str] = None
        self._logger.info("[TimeDispatcher] 폴링 시작")

        while self._running:
            try:
                last_dispatched_date = await self._maybe_dispatch(last_dispatched_date)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[TimeDispatcher] 폴링 오류: {e}", exc_info=True)

            # stop()이 sleep_task를 cancel하면 CancelledError 발생 → 루프 종료
            self._sleep_task = asyncio.create_task(asyncio.sleep(self.POLL_INTERVAL))
            try:
                await self._sleep_task
            except asyncio.CancelledError:
                break  # stop() 호출로 sleep이 취소됨
            finally:
                self._sleep_task = None

        self._logger.info("[TimeDispatcher] 폴링 종료")

    async def _maybe_dispatch(self, last_dispatched_date: Optional[str]) -> Optional[str]:
        """조건 충족 시 등록된 모든 태스크 티켓을 발행하고 새 날짜를 반환한다."""
        if self._market_clock.is_market_operating_hours():
            return last_dispatched_date

        latest_trading_date = await self._mcs.get_latest_trading_date()
        if not latest_trading_date:
            return last_dispatched_date

        if latest_trading_date == last_dispatched_date:
            return last_dispatched_date  # 이미 이 거래일 발행 완료

        self._logger.info(
            f"[TimeDispatcher] 장 마감 감지 (거래일: {latest_trading_date}) — "
            f"{len(self._task_schedule)}개 티켓 발행"
        )
        for task_name, priority in self._task_schedule.items():
            ticket = Ticket(
                priority=priority,
                task_name=task_name,
                payload={"date": latest_trading_date},
            )
            published = await self._broker.publish(ticket)
            if published:
                self._logger.info(f"[TimeDispatcher] 티켓 발행: {task_name} ({latest_trading_date})")
            else:
                self._logger.warning(f"[TimeDispatcher] 티켓 발행 실패 (큐 포화): {task_name}")

        return latest_trading_date

    def stop(self) -> None:
        """폴링 루프를 중단시킨다. sleep 중이어도 sleep_task를 cancel해 즉시 종료."""
        self._running = False
        if self._sleep_task and not self._sleep_task.done():
            self._sleep_task.cancel()
        self._logger.info("[TimeDispatcher] 중단 요청")
