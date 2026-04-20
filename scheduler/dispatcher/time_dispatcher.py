# scheduler/dispatcher/time_dispatcher.py
"""
TimeDispatcher — MarketClock을 1분마다 폴링하여 장 마감 시 Ticket을 발행한다.

- 실제 영업일(MarketCalendarService.get_latest_trading_date)로 날짜 검증
  → 주말/공휴일에는 티켓 미발행
- 동일 거래일에는 1회만 발행 (last_dispatched_date 추적, SQLite 영속화)
  → 시스템 재시작 후에도 당일 중복 발행 방지
- 태스크별 delay_sec: 장 마감 감지 후 해당 시간만큼 대기하고 티켓 발행
- Graceful Stop: stop() 호출 시 폴링 루프 및 대기 중인 발행 태스크 모두 종료
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from typing import Dict, Optional, Set, TYPE_CHECKING

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
        db_path: Optional[str] = None,
    ) -> None:
        self._broker = broker
        self._market_clock = market_clock
        self._mcs = mcs
        self._logger = logger or logging.getLogger(__name__)
        self._task_schedule: Dict[str, int] = {}   # task_name → priority
        self._task_delays: Dict[str, int] = {}     # task_name → delay_sec
        self._running = False
        self._sleep_task: Optional[asyncio.Task] = None
        self._pending_publish_tasks: Set[asyncio.Task] = set()
        self._db_path = db_path or os.path.join("data", "time_dispatcher_state.db")
        self._task_dispatched_dates: Dict[str, str] = self._load_state()  # task_name → date
        self._last_dispatched_at: Optional[float] = None

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS dispatch_state "
                "(key TEXT PRIMARY KEY, value TEXT)"
            )

    def _load_state(self) -> Dict[str, str]:
        self._init_db()
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT key, value FROM dispatch_state").fetchall()
        result = {row[0]: row[1] for row in rows if row[1]}
        if result:
            self._logger.info(f"[TimeDispatcher] 태스크별 발행 이력 복원: {result}")
        return result

    def _save_state(self, task_name: str, date: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dispatch_state (key, value) VALUES (?, ?)",
                (task_name, date),
            )

    def register_task(self, task_name: str, priority: int = TaskPriority.LOW, delay_sec: int = 0) -> None:
        """장 마감 시 발행할 태스크를 등록한다."""
        self._task_schedule[task_name] = priority
        self._task_delays[task_name] = delay_sec
        self._logger.info(
            f"[TimeDispatcher] 태스크 등록: {task_name} (priority={priority}, delay={delay_sec}s)"
        )

    def unregister_task(self, task_name: str) -> None:
        self._task_schedule.pop(task_name, None)
        self._task_delays.pop(task_name, None)

    async def run(self) -> None:
        """장 마감 감지 폴링 루프. BackgroundScheduler가 Task로 실행한다."""
        self._running = True
        self._logger.info("[TimeDispatcher] 폴링 시작")

        while self._running:
            try:
                await self._maybe_dispatch()
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

    async def _maybe_dispatch(self) -> None:
        """조건 충족 시 미발행 태스크의 티켓을 발행한다."""
        if self._market_clock.is_market_operating_hours():
            return

        latest_trading_date = await self._mcs.get_latest_trading_date()
        if not latest_trading_date:
            return

        pending = [
            (name, priority)
            for name, priority in self._task_schedule.items()
            if self._task_dispatched_dates.get(name) != latest_trading_date
        ]
        if not pending:
            return

        self._logger.info(
            f"[TimeDispatcher] 장 마감 감지 (거래일: {latest_trading_date}) — "
            f"{len(pending)}개 티켓 예약"
        )
        self._last_dispatched_at = time.time()

        for task_name, priority in pending:
            # 메모리에 즉시 기록해 같은 세션에서 중복 발행 방지
            self._task_dispatched_dates[task_name] = latest_trading_date
            delay = self._task_delays.get(task_name, 0)
            t = asyncio.create_task(
                self._publish_after_delay(task_name, priority, latest_trading_date, delay)
            )
            self._pending_publish_tasks.add(t)
            t.add_done_callback(self._pending_publish_tasks.discard)

    async def _publish_after_delay(self, task_name: str, priority: int, date: str, delay_sec: int) -> None:
        """delay_sec(초) 대기 후 티켓을 발행한다."""
        if delay_sec > 0:
            self._logger.info(f"[TimeDispatcher] {task_name} — {delay_sec}초 후 발행 예정")
            await asyncio.sleep(delay_sec)
        ticket = Ticket(priority=priority, task_name=task_name, payload={"date": date})
        published = await self._broker.publish(ticket)
        if published:
            self._save_state(task_name, date)  # 발행 성공 후에만 DB 영속화
            self._logger.info(f"[TimeDispatcher] 티켓 발행: {task_name} ({date})")
        else:
            # 메모리에서 제거 → 다음 폴링 주기에 재시도 가능
            self._task_dispatched_dates.pop(task_name, None)
            self._logger.warning(f"[TimeDispatcher] 티켓 발행 실패 (큐 포화): {task_name}")

    def get_status(self) -> dict:
        """TimeDispatcher 현재 상태를 반환한다."""
        market_is_open: Optional[bool] = None
        if self._market_clock is not None:
            try:
                market_is_open = self._market_clock.is_market_operating_hours()
            except Exception:
                pass
        return {
            "task_dispatched_dates": dict(self._task_dispatched_dates),
            "last_dispatched_at": self._last_dispatched_at,
            "market_is_open": market_is_open,
            "registered_tasks": [
                {
                    "name": name,
                    "priority": priority,
                    "delay_sec": self._task_delays.get(name, 0),
                }
                for name, priority in self._task_schedule.items()
            ],
        }

    def stop(self) -> None:
        """폴링 루프와 대기 중인 발행 태스크를 모두 중단시킨다."""
        self._running = False
        if self._sleep_task and not self._sleep_task.done():
            self._sleep_task.cancel()
        for t in list(self._pending_publish_tasks):
            t.cancel()
        self._logger.info("[TimeDispatcher] 중단 요청")
