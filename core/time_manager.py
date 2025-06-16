# core/time_manager.py
import datetime
import time
import pytz
import logging  # 로깅 임포트
from zoneinfo import ZoneInfo


class TimeManager:
    """
    주식 거래와 관련된 시간을 관리하는 클래스입니다.
    시장 개장 시간 확인, 시간 지연 등의 기능을 제공합니다.
    """

    def __init__(self, market_open_time="09:00", market_close_time="15:30", timezone="Asia/Seoul",
                 logger=None):  # logger 인자 추가
        self.market_open_time_str = market_open_time
        self.market_close_time_str = market_close_time
        self.timezone_name = timezone
        self.logger = logger if logger else logging.getLogger(__name__)  # 로거 주입 또는 기본 로거 사용

        try:
            self.market_timezone = pytz.timezone(self.timezone_name)
        except pytz.UnknownTimeZoneError:
            self.logger.error(f"알 수 없는 시간대: {self.timezone_name}. 'Asia/Seoul'로 기본 설정합니다.")  # print 대신 logger 사용
            self.timezone_name = "Asia/Seoul"
            self.market_timezone = pytz.timezone(self.timezone_name)

    def get_current_kst_time(self):
        """현재 한국 시간(KST)을 timezone-aware datetime 객체로 반환합니다."""
        return datetime.datetime.now(self.market_timezone)

    def is_market_open(self):
        """
        현재 시간이 시장 개장 시간 내에 있는지 확인합니다.
        주말(토, 일)과 공휴일은 닫힌 것으로 간주하며, 이는 실제 운영 시간과 다를 수 있습니다.
        """
        now = self.get_current_kst_time()

        if now.weekday() >= 5:
            self.logger.info(
                f"시장 상태 - 주말이므로 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")  # print 대신 logger 사용
            return False

        market_open_dt = self.market_timezone.localize(datetime.datetime(
            now.year, now.month, now.day,
            hour=int(self.market_open_time_str.split(':')[0]),
            minute=int(self.market_open_time_str.split(':')[1]),
            second=0, microsecond=0
        ))
        market_close_dt = self.market_timezone.localize(datetime.datetime(
            now.year, now.month, now.day,
            hour=int(self.market_close_time_str.split(':')[0]),
            minute=int(self.market_close_time_str.split(':')[1]),
            second=0, microsecond=0
        ))

        if market_open_dt <= now <= market_close_dt:
            self.logger.info(
                f"시장 상태 - 시장이 열려 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")  # print 대신 logger 사용
            return True
        else:
            self.logger.info(
                f"시장 상태 - 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 개장: {self.market_open_time_str}, 폐장: {self.market_close_time_str})")  # print 대신 logger 사용
            return False

    def sleep(self, seconds):
        """지정된 시간(초)만큼 프로그램을 일시 중지합니다."""
        self.logger.info(f"{seconds}초 동안 대기합니다.")  # print 대신 logger 사용
        time.sleep(seconds)

    def is_holiday(self):
        """공휴일 여부를 확인합니다. (미구현)"""
        return False
