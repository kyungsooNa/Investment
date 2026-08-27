"""미국장 마켓타이밍 일일 갱신 태스크.

국내 `MarketTimingDailyUpdateTask` 와 실행 흐름(개장 30분 전 창에서 하루 1회 갱신
후 알림)이 동일하므로 상속해서 두 지점만 갈아끼운다.

  - 거래일 판정: `USMarketCalendarService.is_trading_day` (동기, 규칙 기반 NYSE 캘린더).
    국내는 `MarketCalendarService.is_business_day` (비동기 KIS 조회).
  - 갱신 대상: `USMarketRegimeService.refresh_market_timing` — 국내 universe service 와
    같은 시그니처라 부모의 호출부를 그대로 쓴다.

`market_clock` 은 `MarketClock.for_us_equities()` 를 주입한다(개장 09:30 ET 기준).
"""
from __future__ import annotations

import logging
from typing import Optional

from task.background.intraday.market_timing_daily_update_task import MarketTimingDailyUpdateTask


class USMarketTimingDailyUpdateTask(MarketTimingDailyUpdateTask):
    """미국장 국면(QQQ 프록시)을 개장 전 1회 갱신하고 알림을 발행한다."""

    def __init__(
        self,
        *,
        us_market_regime_service,
        market_clock,
        us_market_calendar_service=None,
        check_interval_sec: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(
            # 부모의 `_universe_service` 계약은 `refresh_market_timing(caller, logger)` 뿐이며
            # USMarketRegimeService 가 이를 그대로 만족한다.
            universe_service=us_market_regime_service,
            market_clock=market_clock,
            market_calendar_service=us_market_calendar_service,
            check_interval_sec=check_interval_sec,
            logger=logger,
        )

    @property
    def task_name(self) -> str:
        return "us_market_timing_daily_update"

    async def _check_business_day(self, date_key: str) -> bool:
        return self._mcs.is_trading_day(date_key)
