"""장전 코스피 방향 예측 태스크.

국내 `MarketTimingDailyUpdateTask` 의 실행 흐름(개장 30분 전 창에서 하루 1회, 거래일만)을
상속하고 실행 본문만 `PremarketDirectionService.refresh(notify=True)` 로 바꾼다.
예측이 실패하면 그날을 완료로 표시하지 않아 창 안에서 다음 주기에 다시 시도한다.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from task.background.intraday.market_timing_daily_update_task import MarketTimingDailyUpdateTask


class PremarketDirectionForecastTask(MarketTimingDailyUpdateTask):
    """개장 전 코스피 상승 마감 확률을 1회 계산하고 알림을 발행한다."""

    def __init__(
        self,
        *,
        premarket_direction_service,
        market_clock,
        market_calendar_service=None,
        check_interval_sec: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(
            universe_service=None,
            market_clock=market_clock,
            market_calendar_service=market_calendar_service,
            check_interval_sec=check_interval_sec,
            logger=logger,
        )
        self._service = premarket_direction_service

    @property
    def task_name(self) -> str:
        return "premarket_direction_forecast"

    async def run_once(self) -> Dict:
        date_key = self._market_clock.get_current_kst_time().strftime("%Y%m%d")
        if self._mcs is not None:
            try:
                if not await self._check_business_day(date_key):
                    self._last_result = {"ok": True, "skipped": "non_business_day", "date": date_key}
                    return self._last_result
            except Exception as exc:
                self._logger.warning("%s: business day check failed - %s", self.task_name, exc)
                self._last_result = {"ok": False, "error": str(exc), "date": date_key}
                return self._last_result
        forecast = await self._service.refresh(notify=True)
        if forecast.get("ok"):
            self._last_checked_date = date_key
        self._last_result = {"ok": bool(forecast.get("ok")), "date": date_key, "forecast": forecast}
        return self._last_result
