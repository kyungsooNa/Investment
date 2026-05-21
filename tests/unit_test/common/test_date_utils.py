from datetime import date, datetime

from common.date_utils import normalize_yyyymmdd, previous_trading_day_str


def test_normalize_yyyymmdd_accepts_datetime_and_date():
    assert normalize_yyyymmdd(datetime(2026, 4, 30, 15, 10)) == "20260430"
    assert normalize_yyyymmdd(date(2026, 5, 1)) == "20260501"


def test_normalize_yyyymmdd_handles_blank_and_short_values():
    assert normalize_yyyymmdd(None) == ""
    assert normalize_yyyymmdd("abc") == ""
    assert normalize_yyyymmdd("2026-4") == ""


# ─────────────────────────────────────────────────────────────
# previous_trading_day_str
# ─────────────────────────────────────────────────────────────

def test_previous_trading_day_str_weekday_returns_prior_day():
    # 목요일 → 수요일
    assert previous_trading_day_str(datetime(2026, 5, 21, 10, 0)) == "20260520"
    # 화요일 → 월요일
    assert previous_trading_day_str(datetime(2026, 5, 19, 10, 0)) == "20260518"


def test_previous_trading_day_str_skips_weekend():
    # 월요일 (2026-05-18) → 금요일 (2026-05-15)
    assert previous_trading_day_str(datetime(2026, 5, 18, 10, 0)) == "20260515"
    # 일요일 (2026-05-17) → 금요일 (2026-05-15)
    assert previous_trading_day_str(datetime(2026, 5, 17, 10, 0)) == "20260515"
    # 토요일 (2026-05-16) → 금요일 (2026-05-15)
    assert previous_trading_day_str(datetime(2026, 5, 16, 10, 0)) == "20260515"


def test_previous_trading_day_str_skips_holidays():
    # 2026-05-25(월) 부처님오신날 가정. 화요일 → 금요일 (월요일 휴장 우회)
    holidays = {"20260525"}
    assert previous_trading_day_str(datetime(2026, 5, 26, 10, 0), holidays=holidays) == "20260522"
    # 연속 휴장(목·금) + 주말 → 그 이전 수요일
    holidays = {"20260521", "20260522"}
    # 다음 월요일(2026-05-25)에서 직전 영업일은 수요일(2026-05-20)
    assert previous_trading_day_str(datetime(2026, 5, 25, 10, 0), holidays=holidays) == "20260520"


def test_previous_trading_day_str_ignores_time_of_day():
    # 시각은 결과에 영향을 주지 않는다 (자정/장중/장마감 모두 동일)
    assert previous_trading_day_str(datetime(2026, 5, 21, 0, 0)) == "20260520"
    assert previous_trading_day_str(datetime(2026, 5, 21, 15, 30)) == "20260520"
    assert previous_trading_day_str(datetime(2026, 5, 21, 23, 59)) == "20260520"


def test_previous_trading_day_str_accepts_date_input():
    assert previous_trading_day_str(date(2026, 5, 21)) == "20260520"
    assert previous_trading_day_str(date(2026, 5, 18)) == "20260515"

