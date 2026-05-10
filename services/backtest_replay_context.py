"""Backtest replay context helpers.

기간 백테스트에서는 전략과 유니버스가 현재 장중 시각을 보지 않고,
runner가 순회 중인 과거 날짜/시각을 보도록 clock과 snapshot provider를
명시적으로 주입한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.market_clock import MarketClock


class BacktestMarketClock(MarketClock):
    """MarketClock variant pinned to the current backtest date."""

    def __init__(
        self,
        market_open_time: str = "09:00",
        market_close_time: str = "15:40",
        timezone: str = "Asia/Seoul",
        logger=None,
        *,
        default_time: str = "12:00:00",
    ) -> None:
        super().__init__(
            market_open_time=market_open_time,
            market_close_time=market_close_time,
            timezone=timezone,
            logger=logger,
        )
        self._default_time = default_time
        self._current_dt: datetime | None = None

    @classmethod
    def from_clock(
        cls,
        clock: MarketClock,
        *,
        default_time: str = "12:00:00",
    ) -> "BacktestMarketClock":
        return cls(
            market_open_time=getattr(clock, "market_open_time_str", "09:00"),
            market_close_time=getattr(clock, "market_close_time_str", "15:40"),
            timezone=getattr(clock, "timezone_name", "Asia/Seoul"),
            logger=getattr(clock, "logger", None),
            default_time=default_time,
        )

    def set_backtest_date(self, date_ymd: str) -> None:
        self.set_backtest_datetime(
            datetime.strptime(
                f"{date_ymd} {self._default_time}",
                "%Y%m%d %H:%M:%S",
            )
        )

    def set_backtest_datetime(self, dt: datetime) -> None:
        if dt.tzinfo is None:
            self._current_dt = self.market_timezone.localize(dt)
        else:
            self._current_dt = dt.astimezone(self.market_timezone)

    def get_current_kst_time(self) -> datetime:
        if self._current_dt is None:
            raise ValueError("backtest date is not set")
        return self._current_dt


def apply_backtest_snapshot_context(
    target: Any,
    *,
    stock_query_service: Any,
    market_clock: MarketClock,
) -> None:
    """Patch existing services to use replay data and the backtest clock.

    This is intentionally small and explicit: current O'Neil services keep the
    StockQueryService and MarketClock as `_sqs` and `_tm`.
    """
    if hasattr(target, "_sqs"):
        setattr(target, "_sqs", stock_query_service)
    if hasattr(target, "_tm"):
        setattr(target, "_tm", market_clock)
