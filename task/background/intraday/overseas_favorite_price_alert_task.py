"""미국장 관심종목 등락률 임계치 알림 태스크."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from repositories.favorite_repository import MARKET_OVERSEAS_US


class OverseasFavoritePriceAlertTask(SchedulableTask):
    """미국장은 실시간 스트림 경로가 없어 해외현재가 REST 조회로 등락률을 감시한다."""

    CHECK_INTERVAL_SEC = 60
    FETCH_TIMEOUT_SEC = 5.0
    DEFAULT_EXCHANGE = "NASD"

    def __init__(self, *, favorite_repository, broker, alert_service, market_clock,
                 overseas_stock_code_repository=None, us_market_calendar_service=None,
                 check_interval_sec: Optional[int] = None, logger=None) -> None:
        self._favorite_repository = favorite_repository
        self._broker = broker
        self._alert_service = alert_service
        self._market_clock = market_clock
        self._overseas_stock_code_repository = overseas_stock_code_repository
        self._us_mcs = us_market_calendar_service
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None

    @property
    def task_name(self) -> str:
        return "overseas_favorite_price_alert"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.HIGH

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
        if not self._is_market_open_now():
            return
        symbols = await self._favorite_repository.get_all(market=MARKET_OVERSEAS_US)
        for symbol in symbols:
            price, rate = await self._fetch_price_rate(symbol)
            if rate is None:
                continue
            await self._alert_service.handle_price_tick(symbol, price=price, rate=rate)

    async def _fetch_price_rate(self, symbol: str):
        try:
            response = await asyncio.wait_for(
                self._broker.get_overseas_price(symbol, exchange=self._exchange_of(symbol)),
                timeout=self.FETCH_TIMEOUT_SEC,
            )
        except Exception as exc:
            self._logger.warning("%s: %s 현재가 조회 예외 — %s", self.task_name, symbol, exc)
            return None, None
        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning("%s: %s 현재가 조회 실패 — %s", self.task_name, symbol, response.msg1)
            return None, None
        return getattr(response.data, "price", None), getattr(response.data, "change_rate", None)

    def _exchange_of(self, symbol: str) -> str:
        if self._overseas_stock_code_repository is None:
            return self.DEFAULT_EXCHANGE
        meta = self._overseas_stock_code_repository.get_meta(symbol)
        return (meta or {}).get("exchange") or self.DEFAULT_EXCHANGE

    def _is_market_open_now(self) -> bool:
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._us_mcs is None:
            return True
        return self._us_mcs.is_trading_day(self._market_clock.get_current_kst_date_str())
