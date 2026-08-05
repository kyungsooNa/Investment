# task/background/intraday/overseas_intraday_vbo_task.py
"""해외 장중 VBO 폴링 태스크 (라이브 paper 경로).

미국은 웹소켓/분봉 경로가 없어 해외현재가 REST 폴링이 유일한 장중 틱 소스다
(`OverseasFavoritePriceAlertTask` 와 동일 패턴). 정규장 동안 감시 종목을 폴링해
`OverseasIntradayVBOService` 에 틱을 흘리고, 마감 직전에는 EOD 청산만 수행한다.

TimeDispatcher 미등록 — 마감 이벤트가 아니라 장중 연속 루프이므로 자체 폴링한다.

**주문 경로**: 서비스가 `OverseasOrderExecutionService` 를 통해서만 주문하며,
live_enabled=False(기본)에서는 would-be 만 기록된다 — 실주문 없음.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState


class OverseasIntradayVBOTask(SchedulableTask):
    CHECK_INTERVAL_SEC = 60
    FETCH_TIMEOUT_SEC = 5.0
    DEFAULT_CLOSE_TIME = "16:00"

    def __init__(
        self,
        *,
        vbo_service,
        broker,
        market_clock,
        us_market_calendar_service=None,
        shadow_journal=None,
        check_interval_sec: Optional[int] = None,
        session_prepare_delay_min: int = 5,
        eod_exit_before_min: int = 10,
        logger=None,
    ) -> None:
        self._vbo = vbo_service
        self._broker = broker
        self._market_clock = market_clock
        self._us_mcs = us_market_calendar_service
        self._journal = shadow_journal
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._prepare_delay_min = session_prepare_delay_min
        self._eod_exit_before_min = eod_exit_before_min
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._last_eod_date: Optional[str] = None

    @property
    def task_name(self) -> str:
        return "overseas_intraday_vbo"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> dict:
        return {"running": self._state == TaskState.RUNNING}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    self._state = TaskState.RUNNING
                    await self._tick()
                    if self._state == TaskState.RUNNING:
                        self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error("%s: loop error — %s", self.task_name, exc, exc_info=True)
            await asyncio.sleep(self._check_interval_sec)

    async def _tick(self) -> None:
        now = self._market_clock.get_current_kst_time()
        today = self._market_clock.get_current_kst_date_str()
        if not self._market_clock.is_market_operating_hours(now):
            return
        if self._us_mcs is not None and not self._us_mcs.is_trading_day(today):
            return
        if self._minutes_of_day(now) < self._prepare_ready_minute(now):
            # 개장 직후에는 당일 봉(시가)이 아직 없을 수 있어 세션 준비를 미룬다.
            return

        if self._is_eod_window(now, today):
            if self._last_eod_date != today:
                actions = await self._vbo.close_all(reason="eod")
                self._last_eod_date = today
                self._logger.info({"event": "overseas_intraday_vbo_eod_exit",
                                   "trade_date": today, "exits": len(actions or [])})
                self._flush_journal(today)
            return

        await self._vbo.prepare_session(today)
        for code in self._vbo.watch_codes():
            price = await self._fetch_price(code)
            if price is None:
                continue
            await self._vbo.on_price(code, price)

    def _flush_journal(self, trade_date: str) -> None:
        """세션 paper 기록을 자기 거래일 파일로 내린다.

        저널 버퍼는 국내 shadow·해외 dry-run 과 공유되므로, 직접 flush 하지 않으면
        다른 태스크의 flush 시점·파일명에 기록이 딸려가거나(날짜 오배치) 그 태스크가
        실패하면 통째로 유실된다. flush 실패는 흡수한다 — 청산은 이미 끝났다.
        """
        if self._journal is None:
            return
        try:
            self._journal.flush_to_file(trade_date)
        except Exception as exc:
            self._logger.warning("%s: 저널 flush 실패 — %s", self.task_name, exc)

    async def _fetch_price(self, code: str) -> Optional[float]:
        try:
            resp = await asyncio.wait_for(
                self._broker.get_overseas_price(code), timeout=self.FETCH_TIMEOUT_SEC,
            )
        except Exception as exc:
            self._logger.warning("%s: %s 현재가 조회 예외 — %s", self.task_name, code, exc)
            return None
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return None
        price = getattr(getattr(resp, "data", None), "price", None)
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    # ── 시각 판정 ───────────────────────────────────────────────────────

    @staticmethod
    def _minutes_of_day(dt) -> int:
        return dt.hour * 60 + dt.minute

    def _prepare_ready_minute(self, now) -> int:
        open_dt = self._market_clock.get_market_open_time(now)
        return self._minutes_of_day(open_dt) + self._prepare_delay_min

    def _close_minute(self, today: str) -> int:
        """조기폐장(13:00 ET)을 반영한 당일 마감 분. 캘린더 미주입 시 16:00."""
        close_str = self.DEFAULT_CLOSE_TIME
        if self._us_mcs is not None:
            close_str = self._us_mcs.get_close_time_str(today) or self.DEFAULT_CLOSE_TIME
        try:
            hh, mm = str(close_str).split(":")
            return int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            return 16 * 60

    def _is_eod_window(self, now, today: str) -> bool:
        return self._minutes_of_day(now) >= self._close_minute(today) - self._eod_exit_before_min
