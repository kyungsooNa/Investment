"""미국장 MarketClock 팩토리 테스트 (Phase 3).

MarketClock 은 이미 timezone/open/close 파라미터화돼 있으므로, 미국 정규장
(America/New_York 09:30~16:00, DST는 pytz 자동) 구성 팩토리만 추가한다.
"""
from datetime import datetime
from core.market_clock import MarketClock


def test_for_us_equities_uses_new_york_session():
    clk = MarketClock.for_us_equities()
    assert clk.timezone_name == "America/New_York"
    assert clk.market_open_time_str == "09:30"
    assert clk.market_close_time_str == "16:00"


def test_for_us_equities_current_time_is_tz_aware_new_york():
    clk = MarketClock.for_us_equities()
    now = clk.get_current_kst_time()  # 이름은 KST지만 클럭 tz 기준
    assert now.tzinfo is not None
    assert "New_York" in str(now.tzinfo)


def test_for_us_equities_operating_hours_at_known_instants():
    clk = MarketClock.for_us_equities()
    tz = clk.market_timezone
    # 정규장 중 (10:00 ET, 평일)
    open_dt = tz.localize(datetime(2026, 6, 15, 10, 0))
    # 장 마감 후 (17:00 ET)
    closed_dt = tz.localize(datetime(2026, 6, 15, 17, 0))
    assert clk.is_market_operating_hours(open_dt) is True
    assert clk.is_market_operating_hours(closed_dt) is False
