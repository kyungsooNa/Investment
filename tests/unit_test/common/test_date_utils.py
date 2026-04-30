from datetime import date, datetime

from common.date_utils import normalize_yyyymmdd


def test_normalize_yyyymmdd_accepts_datetime_and_date():
    assert normalize_yyyymmdd(datetime(2026, 4, 30, 15, 10)) == "20260430"
    assert normalize_yyyymmdd(date(2026, 5, 1)) == "20260501"


def test_normalize_yyyymmdd_handles_blank_and_short_values():
    assert normalize_yyyymmdd(None) == ""
    assert normalize_yyyymmdd("abc") == ""
    assert normalize_yyyymmdd("2026-4") == ""

