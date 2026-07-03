from unittest.mock import MagicMock

from services.us_market_calendar_service import USMarketCalendarService


def _svc(clock_date: str = "20260703") -> USMarketCalendarService:
    clock = MagicMock()
    clock.get_current_kst_date_str.return_value = clock_date
    return USMarketCalendarService(market_clock=clock, logger=MagicMock())


class TestFullHolidays2026:
    """2026년 NYSE 전휴장일 규칙 검증."""

    def test_2026_full_holiday_set(self):
        svc = _svc()
        expected_holidays = [
            "20260101",  # 신정 (목)
            "20260119",  # MLK — 1월 3째 월요일
            "20260216",  # 워싱턴 탄생일 — 2월 3째 월요일
            "20260403",  # 성금요일 (부활절 2026-04-05 - 2일)
            "20260525",  # 메모리얼 — 5월 마지막 월요일
            "20260619",  # 준틴스 (금)
            "20260703",  # 독립기념일 관측 (7/4 토 → 금)
            "20260907",  # 노동절 — 9월 1째 월요일
            "20261126",  # 추수감사절 — 11월 4째 목요일
            "20261225",  # 크리스마스 (금)
        ]
        for d in expected_holidays:
            assert svc.is_trading_day(d) is False, f"{d} 는 휴장이어야 함"

    def test_2026_normal_weekdays_are_trading_days(self):
        svc = _svc()
        for d in ("20260702", "20260706", "20261127", "20261224"):
            assert svc.is_trading_day(d) is True, f"{d} 는 거래일이어야 함"

    def test_weekends_are_not_trading_days(self):
        svc = _svc()
        assert svc.is_trading_day("20260704") is False  # 토
        assert svc.is_trading_day("20260705") is False  # 일


class TestObservanceRules:
    def test_new_years_on_saturday_is_not_observed_on_friday(self):
        # 2022-01-01(토) — NYSE 규칙상 전년 12/31(금)로 이동하지 않는다 (2021-12-31 개장).
        svc = _svc()
        assert svc.is_trading_day("20211231") is True

    def test_sunday_holidays_observed_on_monday(self):
        svc = _svc()
        assert svc.is_trading_day("20210705") is False  # 7/4(일) → 월요일 관측
        assert svc.is_trading_day("20221226") is False  # 12/25(일) → 월요일 관측

    def test_good_friday_computus(self):
        svc = _svc()
        assert svc.is_trading_day("20250418") is False  # 부활절 2025-04-20
        assert svc.is_trading_day("20260403") is False  # 부활절 2026-04-05


class TestEarlyClose:
    def test_early_close_days(self):
        svc = _svc()
        assert svc.is_early_close_day("20261127") is True  # 추수감사절 다음날(금)
        assert svc.is_early_close_day("20261224") is True  # 크리스마스 이브(목)
        assert svc.is_early_close_day("20250703") is True  # 7/3(목), 7/4(금) 휴장

    def test_full_holiday_is_not_early_close(self):
        svc = _svc()
        # 2026-07-03 은 관측 전휴장이므로 조기폐장이 아니다.
        assert svc.is_early_close_day("20260703") is False

    def test_normal_day_is_not_early_close(self):
        svc = _svc()
        assert svc.is_early_close_day("20260702") is False

    def test_close_time_str(self):
        svc = _svc()
        assert svc.get_close_time_str("20261127") == "13:00"
        assert svc.get_close_time_str("20260702") == "16:00"


class TestLatestTradingDate:
    async def test_holiday_returns_previous_trading_day(self):
        # 2026-07-03(금, 관측휴장) → 7/2(목)
        svc = _svc(clock_date="20260703")
        assert await svc.get_latest_trading_date() == "20260702"

    async def test_weekend_walks_back_to_friday_unless_holiday(self):
        # 2026-06-20(토) → 6/19 준틴스 휴장 → 6/18(목)
        svc = _svc(clock_date="20260620")
        assert await svc.get_latest_trading_date() == "20260618"

    async def test_trading_day_returns_itself(self):
        svc = _svc(clock_date="20260702")
        assert await svc.get_latest_trading_date() == "20260702"
