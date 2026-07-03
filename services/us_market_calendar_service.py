"""규칙 기반 NYSE 거래 캘린더 (O-1 — 해외 Phase 5 전제 게이트).

KIS Open API에는 해외 휴장일 조회 TR이 없어(국내 CTCA0903R만 존재) NYSE 공식
휴장 규칙을 로컬에서 계산한다. AfterMarketLoop / TimeDispatcher가 기대하는
`get_latest_trading_date()` 인터페이스와 호환되어, 미국장 태스크의 휴장일
스킵과 (Phase 5) 조기폐장 EOD 청산 시각 판단에 사용한다.

반영 규칙:
- 전휴장: 신정(일→월 관측, 토는 무관측 — NYSE 규칙), MLK(1월 3째 월),
  워싱턴 탄생일(2월 3째 월), 성금요일(부활절-2일), 메모리얼(5월 마지막 월),
  준틴스(6/19, 2022년~), 독립기념일(7/4), 노동절(9월 1째 월),
  추수감사절(11월 4째 목), 크리스마스(12/25). 고정일 휴장은 토→금/일→월 관측.
- 조기폐장(13:00 ET): 7/3(월~목요일일 때), 추수감사절 다음날(금), 12/24(월~목요일일 때).

한계: 국장(國葬)·재해 등 임시 특별휴장은 반영하지 않는다.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, Optional, Set

_DATE_FMT = "%Y%m%d"


def _easter_sunday(year: int) -> date:
    """Gregorian computus (Anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """해당 월의 n번째 weekday (월=0)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """해당 월의 마지막 weekday (월=0)."""
    next_month = date(year + (month == 12), (month % 12) + 1, 1)
    d = next_month - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """고정일 휴장의 주말 관측 이동: 토→전일(금), 일→익일(월)."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


class USMarketCalendarService:
    """NYSE 휴장일·조기폐장 판단자. `market_clock`은 America/New_York 클럭을 주입한다."""

    def __init__(self, market_clock, logger: Optional[logging.Logger] = None) -> None:
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._holiday_cache: Dict[int, Set[date]] = {}

    # ── 휴장일 계산 ──

    def _full_holidays(self, year: int) -> Set[date]:
        cached = self._holiday_cache.get(year)
        if cached is not None:
            return cached
        holidays: Set[date] = set()
        new_years = date(year, 1, 1)
        if new_years.weekday() == 6:  # 일 → 월 관측
            holidays.add(new_years + timedelta(days=1))
        elif new_years.weekday() < 5:
            holidays.add(new_years)
        # 토요일 신정은 전년 12/31로 이동하지 않음 (NYSE 규칙)
        holidays.add(_nth_weekday(year, 1, 0, 3))    # MLK
        holidays.add(_nth_weekday(year, 2, 0, 3))    # 워싱턴 탄생일
        holidays.add(_easter_sunday(year) - timedelta(days=2))  # 성금요일
        holidays.add(_last_weekday(year, 5, 0))      # 메모리얼
        if year >= 2022:
            holidays.add(_observed(date(year, 6, 19)))  # 준틴스
        holidays.add(_observed(date(year, 7, 4)))    # 독립기념일
        holidays.add(_nth_weekday(year, 9, 0, 1))    # 노동절
        holidays.add(_nth_weekday(year, 11, 3, 4))   # 추수감사절
        holidays.add(_observed(date(year, 12, 25)))  # 크리스마스
        self._holiday_cache[year] = holidays
        return holidays

    @staticmethod
    def _parse(yyyymmdd: str) -> date:
        return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))

    # ── 공개 API ──

    def is_trading_day(self, yyyymmdd: str) -> bool:
        d = self._parse(yyyymmdd)
        if d.weekday() >= 5:
            return False
        return d not in self._full_holidays(d.year)

    def is_early_close_day(self, yyyymmdd: str) -> bool:
        """13:00 ET 조기폐장 여부. 전휴장일은 False."""
        if not self.is_trading_day(yyyymmdd):
            return False
        d = self._parse(yyyymmdd)
        if d.month == 7 and d.day == 3 and d.weekday() <= 3:
            return True
        if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
            return True
        if d.month == 12 and d.day == 24 and d.weekday() <= 3:
            return True
        return False

    def get_close_time_str(self, yyyymmdd: str) -> str:
        """해당 거래일의 정규장 마감 시각 (ET, "HH:MM"). Phase 5 EOD 청산 판단용."""
        return "13:00" if self.is_early_close_day(yyyymmdd) else "16:00"

    async def get_latest_trading_date(self) -> Optional[str]:
        """US 클럭 기준 오늘 포함 가장 최근 거래일 (YYYYMMDD).

        AfterMarketLoop / TimeDispatcher 의 mcs 인터페이스 호환 (async).
        """
        today_str = self._market_clock.get_current_kst_date_str()
        if not today_str:
            return None
        d = self._parse(today_str)
        for _ in range(15):  # 연말 연휴+주말 최장 조합도 커버
            candidate = d.strftime(_DATE_FMT)
            if self.is_trading_day(candidate):
                return candidate
            d -= timedelta(days=1)
        self._logger.warning(f"USMarketCalendar: {today_str} 기준 최근 거래일 탐색 실패")
        return None
