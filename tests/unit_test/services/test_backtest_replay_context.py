from __future__ import annotations

from datetime import datetime

import pytest

from core.market_clock import MarketClock
from services.backtest_replay_context import (
    BacktestMarketClock,
    apply_backtest_snapshot_context,
)


def test_backtest_market_clock_freezes_date_and_intraday_time_from_yyyymmdd():
    clock = BacktestMarketClock(
        market_open_time="09:00",
        market_close_time="15:30",
        timezone="Asia/Seoul",
        default_time="09:30:00",
    )

    clock.set_backtest_date("20260504")

    assert clock.get_current_kst_time().strftime("%Y%m%d %H:%M:%S") == "20260504 09:30:00"
    assert clock.get_current_kst_date_str() == "20260504"
    assert clock.get_market_open_time().strftime("%Y%m%d %H:%M:%S") == "20260504 09:00:00"
    assert clock.get_market_close_time().strftime("%Y%m%d %H:%M:%S") == "20260504 15:30:00"


def test_backtest_market_clock_accepts_datetime_and_preserves_timezone():
    clock = BacktestMarketClock(default_time="12:00:00")

    clock.set_backtest_datetime(datetime(2026, 5, 4, 10, 15, 30))

    now = clock.get_current_kst_time()
    assert now.strftime("%Y%m%d %H:%M:%S") == "20260504 10:15:30"
    assert str(now.tzinfo) == "Asia/Seoul"


def test_backtest_market_clock_requires_date_before_use():
    clock = BacktestMarketClock()

    with pytest.raises(ValueError, match="backtest date is not set"):
        clock.get_current_kst_time()


def test_backtest_market_clock_can_copy_market_hours_from_live_clock():
    live_clock = MarketClock(
        market_open_time="08:50",
        market_close_time="15:45",
        timezone="Asia/Seoul",
    )

    clock = BacktestMarketClock.from_clock(live_clock, default_time="10:00:00")
    clock.set_backtest_date("20260504")

    assert clock.get_market_open_time().strftime("%H:%M:%S") == "08:50:00"
    assert clock.get_market_close_time().strftime("%H:%M:%S") == "15:45:00"
    assert clock.get_current_kst_time().strftime("%H:%M:%S") == "10:00:00"


def test_apply_backtest_snapshot_context_replaces_clock_and_stock_query_service():
    class Target:
        def __init__(self) -> None:
            self._sqs = object()
            self._tm = object()

    target = Target()
    replay_sqs = object()
    clock = BacktestMarketClock()

    apply_backtest_snapshot_context(
        target,
        stock_query_service=replay_sqs,
        market_clock=clock,
    )

    assert target._sqs is replay_sqs
    assert target._tm is clock
