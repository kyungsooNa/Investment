# scheduler/dispatcher/time_dispatcher.py
"""
TimeDispatcher — MarketClock을 1분마다 폴링하여 장 마감 시 Ticket을 발행한다.

- 실제 영업일(MarketCalendarService.get_latest_trading_date)로 날짜 검증
  → 주말/공휴일에는 티켓 미발행
- 장 마감 후(is_after_market_close)에만 발행 → 장 전 오전 실행 시 발행 방지
- task별 독립 날짜 추적 (SQLite 영속화):
  → 재시작 후 미발행 task만 티켓 승계, 이미 발행된 task는 중복 발행 방지
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
        mcs: Optional["MarketCalendarService"],
        logger: Optional[logging.Logger] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._broker = broker
        self._market_clock = market_clock
        self._mcs = mcs
        self._logger = logger or logging.getLogger(__name__)
        self._task_schedule: Dict[str, int] = {}   # task_name → priority
        self._task_delays: Dict[str, int] = {}     # task_name → delay_sec
        self._task_dispatched_dates: Dict[str, Optional[str]] = {}  # task_name → last dispatched date
        self._last_non_trading_log_key: Optional[str] = None
        self._running = False
        self._sleep_task: Optional[asyncio.Task] = None
        self._pending_publish_tasks: Set[asyncio.Task] = set()
        self._db_path = db_path or os.path.join("data", "time_dispatcher_state.db")
        self._last_dispatched_at: Optional[float] = None
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_dispatch "
                "(task_name TEXT PRIMARY KEY, last_dispatched_date TEXT)"
            )

    def _load_task_date(self, task_name: str) -> Optional[str]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT last_dispatched_date FROM task_dispatch WHERE task_name = ?",
                (task_name,),
            ).fetchone()
        date = row[0] if row else None
        if date:
            self._logger.info(f"[TimeDispatcher] {task_name} 마지막 발행 거래일 복원: {date}")
        return date

    def _save_task_date(self, task_name: str, date: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_dispatch (task_name, last_dispatched_date) VALUES (?, ?)",
                (task_name, date),
            )

    def _is_after_market_close(self) -> bool:
        """현재 시각이 당일 장 마감(15:40) 이후인지 확인한다.
        주말은 항상 True로 보되, mcs 주입 시 비거래일 발행 여부는 _maybe_dispatch가 다시 막는다."""
        now = self._market_clock.get_current_kst_time()
        if now.weekday() >= 5:  # 주말
            return True
        return self._market_clock.get_seconds_until_market_close(now) < 0

    def register_task(self, task_name: str, priority: int = TaskPriority.LOW, delay_sec: int = 0) -> None:
        """장 마감 시 발행할 태스크를 등록하고 DB에서 마지막 발행일을 복원한다."""
        task_name = str(task_name)  # SQLite 바인딩을 위해 str 보장
        self._task_schedule[task_name] = priority
        self._task_delays[task_name] = delay_sec
        self._task_dispatched_dates[task_name] = self._load_task_date(task_name)
        self._logger.info(
            f"[TimeDispatcher] 태스크 등록: {task_name} (priority={priority}, delay={delay_sec}s)"
        )

    def unregister_task(self, task_name: str) -> None:
        self._task_schedule.pop(task_name, None)
        self._task_delays.pop(task_name, None)
        self._task_dispatched_dates.pop(task_name, None)

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
        """조건 충족 시 미발행 태스크 티켓만 선별하여 발행한다."""
        if self._market_clock.is_market_operating_hours():
            return

        # 장 전 오전 실행 방지: 장 마감(15:40) 이후에만 발행
        if not self._is_after_market_close():
            return

        today_str = self._market_clock.get_current_kst_date_str()
        if self._mcs is not None:
            latest_trading_date = await self._mcs.get_latest_trading_date()
            if not latest_trading_date:
                return
            if today_str and latest_trading_date != today_str:
                log_key = f"{today_str}:{latest_trading_date}"
                if self._last_non_trading_log_key != log_key:
                    self._logger.info(
                        f"[TimeDispatcher] 오늘({today_str})은 휴장일/비거래일입니다 — "
                        f"티켓 발행 스킵 (최근 거래일={latest_trading_date})"
                    )
                    self._last_non_trading_log_key = log_key
                return
        else:
            # 거래 캘린더 미주입(해외장 등): clock 날짜를 거래일 식별자로 사용한다.
            # 주말/공휴일은 미반영(현행 AfterMarketLoop mcs=None 동작과 동일한 한계).
            latest_trading_date = today_str
        if not latest_trading_date:
            return

        # task별 독립 체크: 이미 발행된 task는 제외
        tasks_to_dispatch = [
            (name, priority)
            for name, priority in self._task_schedule.items()
            if self._task_dispatched_dates.get(name) != latest_trading_date
        ]

        if not tasks_to_dispatch:
            return

        self._logger.info(
            f"[TimeDispatcher] 장 마감 감지 (거래일: {latest_trading_date}) — "
            f"{len(tasks_to_dispatch)}개 티켓 예약"
        )
        self._last_dispatched_at = time.time()

        # 재시작 시점이 실제 마감 시각보다 한참 뒤일 수 있으므로(예: 앱이 마감 후 몇 시간 뒤에야
        # 기동), delay_sec은 "감지 시각부터"가 아닌 "실제 마감 시각부터" 흐른 것으로 본다.
        # 이미 경과한 시간만큼 차감해, 장중 등 엉뚱한 시간대에 지연 발행되는 것을 방지한다.
        elapsed_since_close = max(0.0, -self._market_clock.get_seconds_until_market_close())

        for task_name, priority in tasks_to_dispatch:
            # 재시작 후 중복 발행 방지를 위해 asyncio.create_task 전에 DB 저장
            self._task_dispatched_dates[task_name] = latest_trading_date
            self._save_task_date(task_name, latest_trading_date)
            delay = max(0.0, self._task_delays.get(task_name, 0) - elapsed_since_close)
            t = asyncio.create_task(
                self._publish_after_delay(task_name, priority, latest_trading_date, delay)
            )
            self._pending_publish_tasks.add(t)
            t.add_done_callback(self._pending_publish_tasks.discard)

    async def _publish_after_delay(self, task_name: str, priority: int, date: str, delay_sec: float) -> None:
        """delay_sec(초) 대기 후 티켓을 발행한다."""
        if delay_sec > 0:
            self._logger.info(f"[TimeDispatcher] {task_name} — {delay_sec}초 후 발행 예정")
            await asyncio.sleep(delay_sec)
        ticket = Ticket(priority=priority, task_name=task_name, payload={"date": date})
        published = await self._broker.publish(ticket)
        if published:
            self._logger.info(f"[TimeDispatcher] 티켓 발행: {task_name} ({date})")
        else:
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
            "last_dispatched_at": self._last_dispatched_at,
            "market_is_open": market_is_open,
            "registered_tasks": [
                {
                    "name": name,
                    "priority": priority,
                    "delay_sec": self._task_delays.get(name, 0),
                    "last_dispatched_date": self._task_dispatched_dates.get(name),
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
