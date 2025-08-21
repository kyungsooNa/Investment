# core/time_manager.py
import time
import pytz
import logging
import asyncio  # 비동기 sleep을 위해 추가
from datetime import datetime, timedelta, date


class TimeManager:
    """
    주식 거래와 관련된 시간을 관리하는 클래스입니다.
    시장 개장 시간 확인, 시간 지연 등의 기능을 제공합니다.
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


    def is_market_open(self, now=None):
        """
        현재 시간이 시장 개장 시간 내에 있는지 확인합니다.
        주말(토, 일)과 공휴일은 닫힌 것으로 간주합니다.
        테스트를 위해 now 인자를 수동으로 주입할 수 있습니다.
        """
        now = now or self.get_current_kst_time()

        if now.weekday() >= 5:
            self.logger.info(f"시장 상태 - 주말이므로 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
            return False

        market_open_dt = self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_open_time_str.split(':')[0]),
            minute=int(self.market_open_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

        market_close_dt = self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_close_time_str.split(':')[0]),
            minute=int(self.market_close_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

        if market_open_dt <= now <= market_close_dt:
            self.logger.info(f"시장 상태 - 시장이 열려 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
            return True
        else:
            self.logger.info(
                f"시장 상태 - 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 개장: {self.market_open_time_str}, 폐장: {self.market_close_time_str})")
            return False

    def get_market_close_time(self):
        """오늘의 시장 폐장 시간 반환"""
        now = self.get_current_kst_time()
        return self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_close_time_str.split(':')[0]),
            minute=int(self.market_close_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

    def get_next_market_open_time(self):
        """
        다음 시장 개장 시간을 계산하여 datetime 객체로 반환합니다.
        주말과 이미 지난 시장 시간을 고려합니다.
        """
        now = self.get_current_kst_time()
        today_open = self.market_timezone.localize(datetime(
            now.year, now.month, now.day,
            hour=int(self.market_open_time_str.split(':')[0]),
            minute=int(self.market_open_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

        if now < today_open:
            next_open = today_open
        else:
            next_day = now.date() + timedelta(days=1)
            while next_day.weekday() >= 5:  # 5: 토요일, 6: 일요일
                next_day += timedelta(days=1)

            next_open = self.market_timezone.localize(datetime(
                next_day.year, next_day.month, next_day.day,
                hour=int(self.market_open_time_str.split(':')[0]),
                minute=int(self.market_open_time_str.split(':')[1]),
                second=0, microsecond=0
            ))

        self.logger.info(f"다음 시장 개장 시간: {next_open.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        return next_open

    def get_latest_market_close_time(self) -> datetime:
        """
        현재 시각 기준 가장 가까운 직전 거래일의 장 마감 시각을 반환.
        """
        current_time = self.get_current_kst_time()
        date_cursor = current_time.date()

        while True:
            close_time = self.get_market_close_time_on(date_cursor)
            if close_time < current_time and not self.is_weekend_or_holiday(close_time):
                return close_time
            date_cursor -= timedelta(days=1)

    def sleep(self, seconds):
        """지정된 시간(초)만큼 프로그램을 일시 중지합니다 (동기)."""
        if seconds > 0:
            self.logger.info(f"{seconds:.2f}초 동안 대기합니다 (동기).")
            time.sleep(seconds)

    async def async_sleep(self, seconds):  # <--- async 버전의 sleep 추가
        """지정된 시간(초)만큼 비동기적으로 프로그램을 일시 중지합니다."""
        if seconds > 0:
            self.logger.info(f"{seconds:.2f}초 동안 대기합니다 (비동기).")
            await asyncio.sleep(seconds)  # <--- asyncio.sleep 사용

    def get_market_close_time_on(self, date: datetime) -> datetime:
        close_hour, close_minute = map(int, self.market_close_time_str.split(":"))
        return self.market_timezone.localize(datetime(date.year, date.month, date.day, close_hour, close_minute))

    def is_weekend_or_holiday(self, date: datetime) -> bool:
        # ✅ 필요 시 공휴일 판단 로직 추가
        return date.weekday() >= 5  # 토, 일

    def to_yyyymmdd(self, val) -> str:
        """여러 타입을 YYYYMMDD 문자열로 안전 변환"""
        if val is None:
            dt = self.get_current_kst_time()

            return dt.strftime("%Y%m%d")
        if isinstance(val, str):
            return val  # 가정: 이미 'YYYYMMDD'
        if isinstance(val, (datetime, date)):
            return val.strftime("%Y%m%d")
        if callable(val):  # 실수로 메서드 자체가 들어온 경우
            return self.to_yyyymmdd(val())
        # 숫자 등 기타
        s = str(val)
        return s

    def to_hhmmss(self, t: str | int) -> str:
        """
        다양한 입력(YYYYMMDDHH, YYYYMMDDHHMM, HH, HHMM 등)을 안전하게 HHMMSS로 정규화.
        규칙:
          - 숫자/문자 어떤 형식이 와도 뒤 6자리만 취한다 (부족하면 0 패딩)
          - HH만 오면 HH0000, HHMM이면 HHMM00
        """
        if t is None:
            t = self.get_current_kst_time()

        s = str(t).strip()
        # 숫자 외 문자가 섞였을 수 있으니 필터링
        s = ''.join(ch for ch in s if ch.isdigit())

        # 뒤 6자리 취하되, 길이가 부족하면 앞에 0 채워 6자리로
        s = s[-6:] if len(s) >= 6 else s.rjust(6, "0")

        # HHMM 혹은 HH만 들어온 케이스를 추가 보정
        # ex) '0930' -> '093000', '09' -> '090000'
        if len(s) == 4:  # HHMM
            return s + "00"
        if len(s) == 2:  # HH
            return s + "0000"

        return s  # 이미 6자리(HHMMSS)