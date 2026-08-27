# task/background/intraday/overseas_intraday_task.py
"""해외 장중 전략 폴링 태스크 (라이브 paper 경로).

미국은 웹소켓/분봉 경로가 없어 해외현재가 REST 폴링이 유일한 장중 틱 소스다
(`OverseasFavoritePriceAlertTask` 와 동일 패턴). 정규장 동안 감시 종목을 폴링해
주입된 전략 서비스들에 틱을 흘리고, 마감 직전에는 EOD 청산만 수행한다.

**여러 전략을 한 폴링 패스로 구동한다.** 전략마다 태스크를 두면 겹치는 심볼을
전략 수만큼 중복 조회해 API 예산을 태운다(top_n=20 × 60초 × 6.5h = 전략당 7,800콜).
감시 심볼의 합집합을 만들어 심볼당 1회만 조회하고, 그 심볼을 보는 전략에만
틱을 전달한다.

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


class OverseasIntradayTask(SchedulableTask):
    CHECK_INTERVAL_SEC = 60
    FETCH_TIMEOUT_SEC = 5.0
    DEFAULT_CLOSE_TIME = "16:00"

    def __init__(
        self,
        *,
        strategy_services,
        broker,
        market_clock,
        us_market_calendar_service=None,
        shadow_journal=None,
        check_interval_sec: Optional[int] = None,
        session_prepare_delay_min: int = 5,
        eod_exit_before_min: int = 10,
        logger=None,
    ) -> None:
        self._strategies = list(strategy_services or [])
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
        return "overseas_intraday"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "strategies": [getattr(s, "STRATEGY_NAME", type(s).__name__) for s in self._strategies],
        }

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
                exits = 0
                for svc in self._strategies:
                    actions = await self._safe(svc, svc.close_all(reason="eod"), "eod_exit")
                    exits += len(actions or [])
                self._last_eod_date = today
                self._logger.info({"event": "overseas_intraday_eod_exit",
                                   "trade_date": today, "exits": exits})
                self._flush_journal(today)
            return

        # 감시 심볼 합집합 → 심볼당 1회 조회 → 그 심볼을 보는 전략에만 fan-out
        watchers: dict[str, list] = {}
        for svc in self._strategies:
            await self._safe(svc, svc.prepare_session(today), "prepare_session")
            for code in svc.watch_codes():
                watchers.setdefault(code, []).append(svc)

        for code, services in watchers.items():
            tick = await self._fetch_tick(code)
            if tick is None:
                continue
            price, volume = tick
            for svc in services:
                await self._safe(svc, svc.on_price(code, price, volume=volume), "on_price")
        # 폴링 패스마다 파일로 내린다 — EOD 까지 메모리에 들고 있으면 세션 중 재시작
        # 한 번에 그날 기록이 통째로 사라진다(2026-08-05 실측 유실).
        self._flush_journal(today)

    async def _safe(self, svc, coro, stage: str):
        """전략 하나가 죽어도 같은 폴링 패스의 나머지는 계속 돌린다."""
        try:
            return await coro
        except Exception as exc:
            self._logger.warning({
                "event": "overseas_intraday_strategy_error", "stage": stage,
                "strategy": getattr(svc, "STRATEGY_NAME", type(svc).__name__),
                "error": str(exc),
            })
            return None

    def _flush_journal(self, trade_date: str) -> None:
        """세션 paper 기록을 자기 거래일 파일로 내린다(버퍼가 비면 no-op).

        저널은 이 경로 전용 인스턴스여야 한다 — 국내 shadow 와 버퍼를 공유하면
        틱마다 flush 할 때 남의 기록이 US 거래일 파일로 딸려간다(배선에서 분리).
        flush 실패는 흡수한다 — 다음 패스에서 다시 시도된다.
        """
        if self._journal is None:
            return
        try:
            self._journal.flush_to_file(trade_date)
        except Exception as exc:
            self._logger.warning("%s: 저널 flush 실패 — %s", self.task_name, exc)

    async def _fetch_tick(self, code: str) -> Optional[tuple]:
        """현재가 스냅샷 1건 → (가격, 누적거래량).

        거래량(`tvol`)은 거래량 조건을 쓰는 전략의 유일한 소스다 — 해외는 분봉이
        없어 이보다 촘촘한 누적 거래량 소스가 없다. 없으면 None 으로 넘겨
        전략이 fail-closed 판정하게 한다.
        """
        try:
            resp = await asyncio.wait_for(
                self._broker.get_overseas_price(code), timeout=self.FETCH_TIMEOUT_SEC,
            )
        except Exception as exc:
            self._logger.warning("%s: %s 현재가 조회 예외 — %s", self.task_name, code, exc)
            return None
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return None
        data = getattr(resp, "data", None)
        try:
            price = float(getattr(data, "price", None))
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        try:
            volume = float(getattr(data, "volume", None))
        except (TypeError, ValueError):
            volume = None
        return price, (volume if volume and volume > 0 else None)

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
