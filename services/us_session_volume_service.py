"""미국 정규장 세션 경과 비율 · 종일 거래량 환산.

장중 전략이 "당일 거래량이 평균 대비 N배인가" 를 판정하려면 종일 거래량이
필요한데, 장중에는 확정값이 없다. 국내 전략들(`OneilSqueezeBreakoutStrategy` 등)은
누적 거래량을 장중 경과 비율로 나눠 종일 거래량을 추정하고, 추정 오차가 큰
시간대에 허들을 차등 적용해 이 문제를 푼다. 본 모듈은 같은 방식을 미국 정규장
세션(09:30~16:00 ET)에 맞춰 이식한 것이다.

미국장 고유 사항 두 가지:
  - 조기폐장(13:00 ET)일에는 세션 길이가 390분이 아니라 210분이라, 마감 시각을
    `USMarketCalendarService` 에서 받아 경과 비율과 오후 구간을 함께 당긴다.
  - 해외는 웹소켓/분봉이 없어 누적 거래량은 현재가 스냅샷의 `tvol` 이 유일한
    소스다(`korea_invest_overseas_stock_api.py`). 폴링 간격만큼 지연이 있다.

순수 계산이며 외부 IO 를 갖지 않는다(캘린더는 규칙 기반 로컬 계산).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

_REGULAR_CLOSE = "16:00"
_OPEN_HOUR, _OPEN_MINUTE = 9, 30


@dataclass(frozen=True)
class VolumeHurdle:
    """시간대별 거래량 허들.

    `threshold` 는 **환산(예상) 거래량** 에 대한 하한이고, `min_actual_volume` 은
    오전장 환산 뻥튀기를 막기 위한 **실거래량** 절대 하한이다(해당 없으면 None).
    """
    threshold: float
    min_actual_volume: Optional[float] = None


class USSessionVolumeService:
    def __init__(
        self,
        us_market_calendar_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        progress_floor: float = 0.05,
        morning_window_min: int = 120,
        afternoon_window_min: int = 60,
        afternoon_boost: float = 0.5,
        morning_min_vol_ratio: float = 0.5,
    ) -> None:
        self._calendar = us_market_calendar_service
        self._logger = logger or logging.getLogger(__name__)
        self._progress_floor = progress_floor
        self._morning_window_min = morning_window_min
        self._afternoon_window_min = afternoon_window_min
        self._afternoon_boost = afternoon_boost
        self._morning_min_vol_ratio = morning_min_vol_ratio

    # ── 세션 시각 ───────────────────────────────────────────────────────

    def _close_time_str(self, trade_date: str) -> str:
        """거래일의 마감 시각. 캘린더 실패는 정규 마감으로 흡수한다 — 판정을 멈추지 않는다."""
        if self._calendar is None:
            return _REGULAR_CLOSE
        try:
            return self._calendar.get_close_time_str(trade_date) or _REGULAR_CLOSE
        except Exception as e:
            self._logger.warning({"event": "us_session_close_time_error",
                                  "trade_date": trade_date, "error": str(e)})
            return _REGULAR_CLOSE

    def _session_bounds(self, now, trade_date: str):
        open_t = now.replace(hour=_OPEN_HOUR, minute=_OPEN_MINUTE, second=0, microsecond=0)
        hh, _, mm = self._close_time_str(trade_date).partition(":")
        try:
            close_t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (TypeError, ValueError):
            close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_t, close_t

    # ── 경과 비율 ───────────────────────────────────────────────────────

    def progress_ratio(self, now, trade_date: str) -> float:
        """정규장 경과 비율 [0.0, 1.0]. 개장 전 0.0, 마감 후 1.0."""
        open_t, close_t = self._session_bounds(now, trade_date)
        total = (close_t - open_t).total_seconds()
        if total <= 0:
            return 0.0
        elapsed = (now - open_t).total_seconds()
        if elapsed <= 0:
            return 0.0
        return min(elapsed / total, 1.0)

    # ── 거래량 환산 ─────────────────────────────────────────────────────

    def project_volume(self, accumulated_volume, now, trade_date: str) -> float:
        """누적 거래량을 종일 거래량으로 환산한다.

        개장 직후에는 경과 비율이 0에 수렴해 환산값이 발산하므로 `progress_floor`
        로 하한을 둔다(국내와 동일한 방어). 환산은 어디까지나 추정이며, 오전장
        과대추정은 `volume_hurdle` 의 실거래량 하한이 함께 막는다.
        """
        vol = self._f(accumulated_volume)
        if vol <= 0:
            return 0.0
        progress = max(self.progress_ratio(now, trade_date), self._progress_floor)
        return vol / progress

    def volume_hurdle(
        self, *, avg_volume, base_multiplier: float, now, trade_date: str,
    ) -> VolumeHurdle:
        """시간대별 거래량 허들을 산출한다."""
        avg = self._f(avg_volume)
        open_t, close_t = self._session_bounds(now, trade_date)
        morning_end = open_t + timedelta(minutes=self._morning_window_min)
        afternoon_start = close_t - timedelta(minutes=self._afternoon_window_min)

        if now < morning_end:
            return VolumeHurdle(
                threshold=avg * base_multiplier,
                min_actual_volume=avg * self._morning_min_vol_ratio,
            )
        if now >= afternoon_start:
            return VolumeHurdle(threshold=avg * (base_multiplier + self._afternoon_boost))
        return VolumeHurdle(threshold=avg * base_multiplier)

    def passes(
        self, *, actual_volume, avg_volume, base_multiplier: float, now, trade_date: str,
    ) -> bool:
        """환산 거래량과 실거래량 하한을 한 번에 판정한다."""
        avg = self._f(avg_volume)
        if avg <= 0:
            return False
        hurdle = self.volume_hurdle(
            avg_volume=avg, base_multiplier=base_multiplier, now=now, trade_date=trade_date,
        )
        actual = self._f(actual_volume)
        if hurdle.min_actual_volume is not None and actual < hurdle.min_actual_volume:
            return False
        return self.project_volume(actual, now, trade_date) >= hurdle.threshold

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
