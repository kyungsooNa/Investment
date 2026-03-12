# core/time_manager.py
import time
import pytz
import logging
import asyncio
from datetime import datetime, timedelta, date

class TimeManager:
    """
    주식 거래와 관련된 '시간(시계)'을 관리하는 클래스입니다.
    순수 시간대 계산 및 포맷 변환, KST 타임존 처리를 담당하며,
    공휴일 및 실제 영업일 판단은 MarketDateManager에서 수행해야 합니다.
    """

    def __init__(self, market_open_time="09:00", market_close_time="15:30", timezone="Asia/Seoul", logger=None):
        self.market_open_time_str = market_open_time
        self.market_close_time_str = market_close_time
        self.timezone_name = timezone
        self.logger = logger if logger else logging.getLogger(__name__)

        try:
            self.market_timezone = pytz.timezone(self.timezone_name)
        except pytz.UnknownTimeZoneError:
            self.logger.error(f"알 수 없는 시간대: {self.timezone_name}. 'Asia/Seoul'로 기본 설정합니다.")
            self.timezone_name = "Asia/Seoul"
            self.market_timezone = pytz.timezone(self.timezone_name)

    def get_current_kst_time(self):
        """현재 한국 시간(KST)을 timezone-aware datetime 객체로 반환합니다."""
        return datetime.now(self.market_timezone)

    def get_current_kst_date_str(self):
        """현재 KST 기준 날짜를 YYYYMMDD 포맷으로 반환합니다."""
        return self.get_current_kst_time().strftime("%Y%m%d")

    def is_market_operating_hours(self, now=None) -> bool:
        """
        단순히 현재 '시간'이 시장 운영 시간(예: 09:00 ~ 15:30) 내에 있는지 확인합니다.
        (주의: 공휴일, 임시휴일 등 '영업일' 여부는 MarketDateManager에서 판단해야 합니다.)
        """
        now = now or self.get_current_kst_time()

        # 주말(토, 일)은 기본적으로 1차 제외
        if now.weekday() >= 5:
            return False

        market_open_dt = self.get_market_open_time()
        market_close_dt = self.get_market_close_time()

        return market_open_dt <= now <= market_close_dt

    def get_market_open_time(self) -> datetime:
        """오늘 날짜 기준 시장 개장 시간(09:00) 반환"""
        now = self.get_current_kst_time()
        return self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_open_time_str.split(':')[0]),
            minute=int(self.market_open_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

    def get_market_close_time(self) -> datetime:
        """오늘 날짜 기준 시장 마감 시간(15:30) 반환"""
        now = self.get_current_kst_time()
        return self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_close_time_str.split(':')[0]),
            minute=int(self.market_close_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

    def get_market_close_time_on(self, target_date: datetime) -> datetime:
        """특정 날짜의 시장 마감 시간을 반환합니다."""
        close_hour, close_minute = map(int, self.market_close_time_str.split(":"))
        return self.market_timezone.localize(datetime(
            target_date.year, target_date.month, target_date.day, 
            close_hour, close_minute
        ))

    def get_seconds_until_market_close(self, now=None) -> float:
        """
        현재 시간부터 오늘 장 마감(15:30)까지 남은 초(seconds)를 계산합니다.
        (장 마감 후 계산 시 음수가 반환될 수 있습니다.)
        """
        now = now or self.get_current_kst_time()
        close_time = self.get_market_close_time()
        diff = (close_time - now).total_seconds()
        return diff

    def get_sleep_seconds_until_market_close(self, now=None) -> float:
        """
        현재 시간부터 오늘 장 마감(15:30)까지 대기해야 할 남은 초를 반환합니다.
        이미 마감 시간을 지났다면 0.0을 반환합니다.
        """
        diff = self.get_seconds_until_market_close(now)
        return max(0.0, diff)

    def sleep(self, seconds):
        """지정된 시간(초)만큼 프로그램을 일시 중지합니다 (동기)."""
        if seconds > 0:
            self.logger.info(f"{seconds:.2f}초 동안 대기합니다 (동기).")
            time.sleep(seconds)

    async def async_sleep(self, seconds):
        """지정된 시간(초)만큼 비동기적으로 프로그램을 일시 중지합니다."""
        if seconds > 0:
            self.logger.info(f"{seconds:.2f}초 동안 대기합니다 (비동기).")
            await asyncio.sleep(seconds)

    def to_yyyymmdd(self, val) -> str:
        """여러 타입을 YYYYMMDD 문자열로 안전 변환"""
        if val is None:
            return self.get_current_kst_date_str()
        if isinstance(val, str):
            return val
        if isinstance(val, (datetime, date)):
            return val.strftime("%Y%m%d")
        if callable(val):
            return self.to_yyyymmdd(val())
        return str(val)

    def to_hhmmss(self, t: str | int) -> str:
        """
        다양한 입력(YYYYMMDDHH, YYYYMMDDHHMM, HH, HHMM 등)을 안전하게 HHMMSS로 정규화.
        규칙:
          - 긴 포맷은 뒤 6자리만 취함
          - HH만 오면 HH0000, HHMM이면 HHMM00
          - 애매한 길이(1/3/5자)는 왼쪽 0 패딩
        """
        if t is None:
            t = self.get_current_kst_time()

        s = ''.join(ch for ch in str(t).strip() if ch.isdigit())

        if len(s) == 2:  # HH
            return s + "0000"
        if len(s) == 4:  # HHMM
            return s + "00"

        if len(s) >= 6:
            return s[-6:]
        return s.rjust(6, "0")

    def dec_minute(self, hhmmss: str, minutes: int = 1) -> str:
        """HHMMSS 포맷의 문자열 시간에서 특정 분(minute)을 뺀 시간을 반환합니다."""
        hh = int(hhmmss[0:2])
        mm = int(hhmmss[2:4])
        ss = int(hhmmss[4:6])
        dt = datetime(2000, 1, 1, hh, mm, ss) - timedelta(minutes=minutes)
        return dt.strftime("%H%M%S")