# scheduler/dispatcher/time_dispatcher.py
"""
TimeDispatcher — MarketClock을 1분마다 폴링하여 장 마감 시 Ticket을 발행한다.

- 실제 영업일(MarketCalendarService.get_latest_trading_date)로 날짜 검증
  → 주말/공휴일에는 티켓 미발행
- 장 마감 후(is_after_market_close)에만 발행 → 장 전 오전 실행 시 발행 방지
- task별 독립 날짜 추적 (SQLite 영속화):
  → 재시작 후 미발행 task만 티켓 승계, 이미 발행된 task는 중복 발행 방지
- 장애 복구 정책을 task별로 선택:
  → 현재 데이터 기반 task는 최신 거래일 1회, 날짜 기반 task는 누락 거래일별 발행
- 태스크별 delay_sec: 장 마감 감지 후 해당 시간만큼 대기하고 티켓 발행
- Graceful Stop: stop() 호출 시 폴링 루프 및 대기 중인 발행 태스크 모두 종료
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
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
    MAX_CATCHUP_CALENDAR_DAYS: int = 14
    MAX_CATCHUP_TRADING_DAYS: int = 5

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
        self._task_catchup_latest: Dict[str, bool] = {}
        self._task_catchup_missed: Dict[str, bool] = {}
        self._daily_task_schedule: Dict[str, dict] = {}
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

    def register_task(
        self,
        task_name: str,
        priority: int = TaskPriority.LOW,
        delay_sec: int = 0,
        *,
        catchup_latest: bool = False,
        catchup_missed: bool = False,
    ) -> None:
        """장 마감 태스크와 장애 복구 정책을 등록하고 마지막 발행일을 복원한다."""
        task_name = str(task_name)  # SQLite 바인딩을 위해 str 보장
        self._task_schedule[task_name] = priority
        self._task_delays[task_name] = delay_sec
        self._task_catchup_latest[task_name] = bool(catchup_latest)
        self._task_catchup_missed[task_name] = bool(catchup_missed)
        self._task_dispatched_dates[task_name] = self._load_task_date(task_name)
        self._logger.info(
            f"[TimeDispatcher] 태스크 등록: {task_name} "
            f"(priority={priority}, delay={delay_sec}s, "
            f"catchup_latest={bool(catchup_latest)}, catchup_missed={bool(catchup_missed)})"
        )

    def register_daily_task(
        self,
        task_name: str,
        priority: int = TaskPriority.LOW,
        *,
        hour: int,
        minute: int,
        catchup_window_sec: int = POLL_INTERVAL,
    ) -> None:
        """매일 특정 시각 이후 catchup 창 안에서 티켓을 발행할 태스크를 등록한다."""
        task_name = str(task_name)
        self._daily_task_schedule[task_name] = {
            "priority": priority,
            "hour": int(hour),
            "minute": int(minute),
            "catchup_window_sec": int(catchup_window_sec),
        }
        self._task_dispatched_dates[task_name] = self._load_task_date(task_name)
        self._logger.info(
            f"[TimeDispatcher] daily 태스크 등록: {task_name} "
            f"(priority={priority}, at={int(hour):02d}:{int(minute):02d}, "
            f"catchup={int(catchup_window_sec)}s)"
        )

    def unregister_task(self, task_name: str) -> None:
        self._task_schedule.pop(task_name, None)
        self._task_delays.pop(task_name, None)
        self._task_catchup_latest.pop(task_name, None)
        self._task_catchup_missed.pop(task_name, None)
        self._daily_task_schedule.pop(task_name, None)
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
        await self._maybe_dispatch_daily_tasks()

        if not self._task_schedule:
            return

        if self._market_clock.is_market_operating_hours():
            return

        today_str = self._market_clock.get_current_kst_date_str()
        if self._mcs is not None:
            latest_trading_date = await self._mcs.get_latest_trading_date()
            if not latest_trading_date:
                return
            is_historical_close = bool(today_str and latest_trading_date != today_str)
            if is_historical_close:
                log_key = f"{today_str}:{latest_trading_date}"
                if self._last_non_trading_log_key != log_key:
                    self._logger.info(
                        f"[TimeDispatcher] 오늘({today_str})은 휴장일/비거래일입니다 — "
                        f"티켓 발행 스킵 (최근 거래일={latest_trading_date})"
                    )
                    self._last_non_trading_log_key = log_key
        else:
            # 거래 캘린더 미주입(해외장 등): clock 날짜를 거래일 식별자로 사용한다.
            # 주말/공휴일은 미반영(현행 AfterMarketLoop mcs=None 동작과 동일한 한계).
            latest_trading_date = today_str
        if not latest_trading_date:
            return

        is_historical_close = bool(today_str and latest_trading_date != today_str)
        if not is_historical_close and not self._is_after_market_close():
            # 당일 장 전에는 당일 티켓을 발행하지 않는다. 다만 직전 거래일이 별도로
            # 확인된 경우에는 opt-in 배치 태스크의 장애 복구 catch-up을 허용한다.
            return

        dispatch_plans = []
        for task_name, priority in self._task_schedule.items():
            last_dispatched = self._task_dispatched_dates.get(task_name)
            if last_dispatched == latest_trading_date:
                continue
            catchup_latest = self._task_catchup_latest.get(task_name, False)
            catchup_missed = self._task_catchup_missed.get(task_name, False)
            if is_historical_close and not (catchup_latest or catchup_missed):
                continue
            if catchup_missed:
                dates = await self._get_missed_trading_dates(
                    last_dispatched, latest_trading_date
                )
            else:
                dates = [latest_trading_date]
            if dates:
                dispatch_plans.append((task_name, priority, dates))

        if not dispatch_plans:
            return

        self._logger.info(
            f"[TimeDispatcher] 장 마감 감지 (거래일: {latest_trading_date}) — "
            f"{sum(len(dates) for _, _, dates in dispatch_plans)}개 티켓 예약"
        )
        self._last_dispatched_at = time.time()

        # 재시작 시점이 실제 마감 시각보다 한참 뒤일 수 있으므로(예: 앱이 마감 후 몇 시간 뒤에야
        # 기동), delay_sec은 "감지 시각부터"가 아닌 "실제 마감 시각부터" 흐른 것으로 본다.
        # 이미 경과한 시간만큼 차감해, 장중 등 엉뚱한 시간대에 지연 발행되는 것을 방지한다.
        elapsed_since_close = max(0.0, -self._market_clock.get_seconds_until_market_close())

        for task_name, priority, dates in dispatch_plans:
            # 인메모리 표시는 즉시 — 폴링 루프가 대기 중에 다시 돌아도 중복 예약을 막는다.
            # 반면 DB 저장은 실제 발행에 성공한 뒤에만 한다(_publish_date_sequence).
            # delay_sec 가 큰 태스크는 감지와 발행 사이가 길어(overseas_dryrun=1800s),
            # 그 창에서 죽었을 때 '발행됨'이 먼저 적혀 있으면 재기동 후 dedup 이 그
            # 거래일을 영영 건너뛴다. 실제로 거래일 20260908 해외 dry-run 이 그렇게 유실됐다.
            self._task_dispatched_dates[task_name] = dates[-1]
            delay = 0.0
            if not is_historical_close:
                delay = max(0.0, self._task_delays.get(task_name, 0) - elapsed_since_close)
            t = asyncio.create_task(
                self._publish_date_sequence(
                    task_name,
                    priority,
                    dates,
                    delay,
                    today_str=today_str,
                )
            )
            self._pending_publish_tasks.add(t)
            t.add_done_callback(self._pending_publish_tasks.discard)

    async def _get_missed_trading_dates(
        self, last_dispatched: Optional[str], latest_trading_date: str
    ) -> list[str]:
        """마지막 발행 다음 날부터 최신 거래일까지의 거래일을 제한적으로 복원한다."""
        if not last_dispatched:
            return [latest_trading_date]
        if last_dispatched >= latest_trading_date:
            return []

        latest = datetime.strptime(latest_trading_date, "%Y%m%d")
        first = datetime.strptime(last_dispatched, "%Y%m%d") + timedelta(days=1)
        earliest = latest - timedelta(days=self.MAX_CATCHUP_CALENDAR_DAYS - 1)
        if first < earliest:
            self._logger.warning(
                f"[TimeDispatcher] catch-up 범위 제한: {last_dispatched} 이후 전체가 아닌 "
                f"최근 {self.MAX_CATCHUP_CALENDAR_DAYS}일만 확인합니다."
            )
            first = earliest

        dates: list[str] = []
        cursor = first
        while cursor <= latest:
            candidate = cursor.strftime("%Y%m%d")
            is_business_day = True
            checker = getattr(self._mcs, "is_business_day", None)
            if callable(checker):
                result = checker(candidate)
                if inspect.isawaitable(result):
                    result = await result
                is_business_day = bool(result)
            elif cursor.weekday() >= 5:
                is_business_day = False
            if is_business_day:
                dates.append(candidate)
            cursor += timedelta(days=1)

        return dates[-self.MAX_CATCHUP_TRADING_DAYS:]

    async def _publish_date_sequence(
        self,
        task_name: str,
        priority: int,
        dates: list[str],
        delay_sec: float,
        *,
        today_str: Optional[str],
    ) -> None:
        """한 태스크의 누락 거래일을 오래된 날짜부터 순서대로 발행한다."""
        for index, date in enumerate(dates):
            if index == len(dates) - 1 and delay_sec > 0:
                self._logger.info(f"[TimeDispatcher] {task_name} — {delay_sec}초 후 발행 예정")
                await asyncio.sleep(delay_sec)
            catchup = date != today_str
            payload = {"date": date}
            if catchup:
                payload["catchup"] = True
            ticket = Ticket(
                priority=priority,
                task_name=task_name,
                payload=payload,
            )
            published = await self._broker.publish(ticket)
            if not published:
                self._task_dispatched_dates[task_name] = self._load_task_date(task_name)
                self._logger.warning(f"[TimeDispatcher] 티켓 발행 실패 (큐 포화): {task_name}")
                return
            self._save_task_date(task_name, date)
            if catchup:
                self._logger.info(f"[TimeDispatcher] 누락 거래일 catch-up 티켓 발행: {task_name} ({date})")
            else:
                self._logger.info(f"[TimeDispatcher] 티켓 발행: {task_name} ({date})")

    async def _maybe_dispatch_daily_tasks(self) -> None:
        if not self._daily_task_schedule:
            return
        now = self._market_clock.get_current_kst_time()
        today_str = self._market_clock.get_current_kst_date_str()
        if not today_str:
            return

        for task_name, schedule in list(self._daily_task_schedule.items()):
            if self._task_dispatched_dates.get(task_name) == today_str:
                continue
            due_at = now.replace(
                hour=schedule["hour"],
                minute=schedule["minute"],
                second=0,
                microsecond=0,
            )
            catchup_window = max(0, int(schedule.get("catchup_window_sec", self.POLL_INTERVAL)))
            if not (due_at <= now < due_at + timedelta(seconds=catchup_window)):
                continue

            self._task_dispatched_dates[task_name] = today_str
            scheduled_time = f"{schedule['hour']:02d}:{schedule['minute']:02d}"
            t = asyncio.create_task(
                self._publish_daily_task(
                    task_name,
                    int(schedule["priority"]),
                    today_str,
                    scheduled_time,
                )
            )
            self._pending_publish_tasks.add(t)
            t.add_done_callback(self._pending_publish_tasks.discard)

    async def _publish_daily_task(
        self, task_name: str, priority: int, date: str, scheduled_time: str
    ) -> None:
        ticket = Ticket(
            priority=priority,
            task_name=task_name,
            payload={"date": date, "scheduled_time": scheduled_time},
        )
        published = await self._broker.publish(ticket)
        if published:
            self._save_task_date(task_name, date)
            self._logger.info(
                f"[TimeDispatcher] daily 티켓 발행: {task_name} ({date} {scheduled_time})"
            )
        else:
            self._logger.warning(f"[TimeDispatcher] 티켓 발행 실패 (큐 포화): {task_name}")

    async def _publish_after_delay(self, task_name: str, priority: int, date: str, delay_sec: float) -> None:
        """delay_sec(초) 대기 후 티켓을 발행한다."""
        if delay_sec > 0:
            self._logger.info(f"[TimeDispatcher] {task_name} — {delay_sec}초 후 발행 예정")
            await asyncio.sleep(delay_sec)
        ticket = Ticket(priority=priority, task_name=task_name, payload={"date": date})
        published = await self._broker.publish(ticket)
        if published:
            # 발행에 성공한 뒤에만 영속화한다 — 여기까지 오지 못하고 죽었다면
            # 재기동 후 같은 거래일이 다시 발행되어야 한다.
            self._save_task_date(task_name, date)
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
                    "catchup_latest": self._task_catchup_latest.get(name, False),
                    "catchup_missed": self._task_catchup_missed.get(name, False),
                    "last_dispatched_date": self._task_dispatched_dates.get(name),
                }
                for name, priority in self._task_schedule.items()
            ],
            "daily_registered_tasks": [
                {
                    "name": name,
                    "priority": config["priority"],
                    "hour": config["hour"],
                    "minute": config["minute"],
                    "catchup_window_sec": config["catchup_window_sec"],
                    "last_dispatched_date": self._task_dispatched_dates.get(name),
                }
                for name, config in self._daily_task_schedule.items()
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
