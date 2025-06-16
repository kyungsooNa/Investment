# core/time_manager.py
import datetime
import time
import pytz  # pytz 라이브러리 임포트
from zoneinfo import ZoneInfo  # Python 3.9+에서 ZoneInfo 사용 (pytz와 함께 사용 권장)


class TimeManager:
    """
    주식 거래와 관련된 시간을 관리하는 클래스입니다.
    시장 개장 시간 확인, 시간 지연 등의 기능을 제공합니다.
    """

    def __init__(self, market_open_time="09:00", market_close_time="15:30", timezone="Asia/Seoul"):
        """
        TimeManager를 초기화합니다.
        :param market_open_time: 시장 개장 시간 (HH:MM 형식 문자열)
        :param market_close_time: 시장 폐장 시간 (HH:MM 형식 문자열)
        :param timezone: 시장이 위치한 시간대 (예: "Asia/Seoul")
        """
        self.market_open_time_str = market_open_time  # 문자열로 저장
        self.market_close_time_str = market_close_time  # 문자열로 저장
        self.timezone_name = timezone

        try:
            # pytz를 사용하여 시간대 객체 생성
            self.market_timezone = pytz.timezone(self.timezone_name)
        except pytz.UnknownTimeZoneError:
            print(f"ERROR: 알 수 없는 시간대: {self.timezone_name}. 'Asia/Seoul'로 기본 설정합니다.")
            self.timezone_name = "Asia/Seoul"
            self.market_timezone = pytz.timezone(self.timezone_name)

    def get_current_kst_time(self):
        """현재 한국 시간(KST)을 timezone-aware datetime 객체로 반환합니다."""
        # pytz를 사용하여 현재 시간을 지정된 시간대로 변환
        return datetime.datetime.now(self.market_timezone)

    def is_market_open(self):
        """
        현재 시간이 시장 개장 시간 내에 있는지 확인합니다.
        주말(토, 일)과 공휴일은 닫힌 것으로 간주하며, 이는 실제 운영 시간과 다를 수 있습니다.
        """
        now = self.get_current_kst_time()  # timezone-aware 현재 시간

        # 1. 주말 확인 (토요일=5, 일요일=6)
        if now.weekday() >= 5:
            print(f"INFO: 시장 상태 - 주말이므로 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
            return False

        # 2. 시장 시간 확인 (timezone-aware datetime 객체 생성)
        # now 객체의 날짜 정보를 사용하여 market_open_dt와 market_close_dt를 생성
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
            print(f"INFO: 시장 상태 - 시장이 열려 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')})")
            return True
        else:
            print(
                f"INFO: 시장 상태 - 시장이 닫혀 있습니다. (현재: {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 개장: {self.market_open_time_str}, 폐장: {self.market_close_time_str})")
            return False

    def sleep(self, seconds):
        """지정된 시간(초)만큼 프로그램을 일시 중지합니다."""
        print(f"INFO: {seconds}초 동안 대기합니다.")
        time.sleep(seconds)

    # 필요에 따라 공휴일 확인 등 추가 기능 구현 가능
    def is_holiday(self):
        """공휴일 여부를 확인합니다. (미구현)"""
        # 실제 구현 시 공휴일 API 또는 데이터베이스 연동 필요
        return False
