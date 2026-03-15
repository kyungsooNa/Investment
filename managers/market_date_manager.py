import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from common.types import ResCommonResponse
from core.performance_manager import PerformanceManager

class MarketDateManager:
    """
    주식 시장의 개장일, 휴장일, 과거 최신 영업일 및 다음 개장 시간을 통합 관리하는 달력(Calendar) 매니저입니다.
    """
    def __init__(self, time_manager, logger=None, performance_manager: Optional[PerformanceManager] = None):
        self._time_manager = time_manager
        self._logger = logger or logging.getLogger(__name__)
        self._broker = None
        self._pm = performance_manager if performance_manager else PerformanceManager(enabled=False)
        
        # [과거/현재] get_latest_trading_date 캐시 변수 (기존 로직 유지)
        self._cached_date = None
        self._last_check_date = None
        
        # [미래/달력] check_holiday API 기반 휴장일 캐시 변수 (신규 추가)
        self._business_days_cache = {}
        self._last_sync_month = None

    def set_broker(self, broker):
        self._broker = broker

    # ==============================================================================
    # 1. 과거/현재 기준 최신 영업일 조회 (기존 구현 완벽 유지 -> 기존 TC 통과 보장)
    # ==============================================================================
    async def get_latest_trading_date(self) -> Optional[str]:
        """오늘을 포함하여 가장 최근에 장이 열렸던 영업일(YYYYMMDD)을 반환합니다."""
        current_date = self._time_manager.get_current_kst_time().strftime("%Y%m%d")

        # 캐시가 있고, 오늘 이미 확인했다면 캐시 반환
        if self._cached_date and self._last_check_date == current_date:
            return self._cached_date

        if not self._broker:
            self._logger.warning("MarketDateManager: Broker is not set.")
            return None

        t_start = self._pm.start_timer()
        try:
            latest_date = await self._fetch_from_api()
            if latest_date:
                self._cached_date = latest_date
                self._last_check_date = current_date
            self._pm.log_timer("MarketDateManager.get_latest_trading_date", t_start)
            return latest_date
        except Exception as e:
            self._logger.error(f"Failed to fetch latest trading date: {e}")
            self._pm.log_timer("MarketDateManager.get_latest_trading_date [예외]", t_start)
            return None

    async def _fetch_from_api(self) -> Optional[str]:
        """삼성전자(005930) 일봉 조회를 통해 가장 최근 영업일을 API에서 가져옵니다."""
        if not self._broker:
            self._logger.warning("MarketDateManager: Broker가 설정되지 않았습니다.")
            return None
            
        now = self._time_manager.get_current_kst_time()
        end_dt = now.strftime("%Y%m%d")
        # 7일 전부터 오늘까지 조회 (명절 연휴 등을 감안)
        start_dt = (now - timedelta(days=7)).strftime("%Y%m%d")
        
        try:
            # 1. 내부 클래스에 직접 접근하지 않고, 상위 래퍼(BrokerAPIWrapper)의 메서드를 활용
            # 2. 파라미터를 키워드 인자(kwargs)로 명시하여 안전하게 전달
            resp = await self._broker.inquire_daily_itemchartprice(
                stock_code="005930", 
                start_date=start_dt, 
                end_date=end_dt, 
                fid_period_div_code="D"
            )
            
            # API 호출이 실패한 경우 원인 로깅
            if resp.rt_cd != "0":
                self._logger.error(f"일봉 조회 API 실패: {resp.msg1} (코드: {resp.rt_cd})")
                return None
                
            if resp.data:
                first_item = resp.data[0]
                # 3. 응답 데이터가 딕셔너리인지, 객체(Dataclass)인지 판별하여 안전하게 추출
                if isinstance(first_item, dict):
                    return first_item.get("stck_bsop_date")
                else:
                    # 객체 형태인 경우 getattr 사용
                    return getattr(first_item, "stck_bsop_date", None)
                    
            return None
            
        except Exception as e:
            self._logger.error(f"최근 영업일 일봉 조회 중 예외 발생: {e}", exc_info=True)
            return None

    # ==============================================================================
    # 2. 휴장일 판별 및 미래 개장일 계산 (chk-holiday API 활용 신규 로직)
    # ==============================================================================
    async def _sync_calendar_if_needed(self, target_date: Optional[datetime] = None):
        """특정 날짜가 속한 '월'의 달력 데이터가 캐시에 없다면 API를 호출해 동기화합니다."""
        if target_date is None:
            target_date = self._time_manager.get_current_kst_time()

        target_month = target_date.strftime("%Y%m")

        # 이미 이번 달 데이터를 가져왔고, 해당 날짜가 캐시에 있다면 스킵
        if self._last_sync_month == target_month and target_date.strftime("%Y%m%d") in self._business_days_cache:
            return

        if not self._broker:
            self._logger.error("Broker가 설정되지 않아 휴장일 API를 호출할 수 없습니다.")
            return

        t_start = self._pm.start_timer()
        # 한투 '국내휴장일조회' API 호출
        target_date_str = target_date.strftime("%Y%m%d")
        holiday_data: ResCommonResponse = await self._broker.check_holiday(target_date_str)

        if holiday_data and holiday_data.rt_cd == "0" and holiday_data.data and "output" in holiday_data.data:
            for day_info in holiday_data.data["output"]:
                date_str = day_info["bass_dt"]
                # 영업일이면서 거래일이어야 개장일
                is_open = (day_info["bzdy_yn"] == "Y" and day_info["tr_day_yn"] == "Y")
                self._business_days_cache[date_str] = is_open

        self._last_sync_month = target_month
        self._pm.log_timer(f"MarketDateManager._sync_calendar_if_needed({target_date_str})", t_start)

    async def is_business_day(self, date_str: str = None) -> bool:
        """특정 날짜(YYYYMMDD)가 공휴일/휴장일이 아닌 영업일인지 확인합니다."""
        if not date_str:
            date_str = self._time_manager.get_current_kst_time().strftime("%Y%m%d")
            
        target_date = datetime.strptime(date_str, "%Y%m%d")
        
        # [최적화 1] 주말(토, 일)은 무조건 휴장일이므로 캐시/API 확인 스킵
        if target_date.weekday() >= 5:
            return False
            
        await self._sync_calendar_if_needed(target_date)
        
        return self._business_days_cache.get(date_str, False)

    async def is_market_open_now(self) -> bool:
        """현재 시점이 휴일이 아니며, 장 운영 시간(09:00~15:30) 이내인지 확인합니다."""
        # 장 운영 시간이 아니면 달력(API/캐시)을 확인할 필요도 없이 바로 False 반환 (성능 최적화)
        if not self._time_manager.is_market_operating_hours():
            return False
        return await self.is_business_day()

    async def get_next_open_day(self, current_date_str: str = None) -> str:
        """기준일의 '다음 영업일(YYYYMMDD)'을 반환합니다 (연휴 완벽 스킵)."""
        if not current_date_str:
            current_date_str = self._time_manager.get_current_kst_time().strftime("%Y%m%d")
            
        check_dt = datetime.strptime(current_date_str, "%Y%m%d") + timedelta(days=1)
        
        # 최대 15일 탐색 (긴 명절 연휴 커버)
        for _ in range(15):
            # [최적화 2] 주말이면 달력 동기화 검사를 스킵하고 다음 날로 이동
            if check_dt.weekday() >= 5:
                check_dt += timedelta(days=1)
                continue

            await self._sync_calendar_if_needed(check_dt)
            check_str = check_dt.strftime("%Y%m%d")
            
            if self._business_days_cache.get(check_str) is True:
                return check_str
                
            check_dt += timedelta(days=1)
            
        return current_date_str

    async def get_next_open_time(self) -> datetime:
        """다음 장이 열리는 정확한 '시간(datetime)'을 반환합니다."""
        now = self._time_manager.get_current_kst_time()
        today_str = now.strftime("%Y%m%d")
        
        # 오늘이 영업일인데 아직 장 시작 전(09:00 이전)이라면 오늘이 개장일임
        if await self.is_business_day(today_str) and now < self._time_manager.get_market_open_time():
            next_open_str = today_str
        else:
            # 이미 장이 끝났거나 장 중이라면, 혹은 휴일이라면 다음 영업일을 찾음
            next_open_str = await self.get_next_open_day(today_str)
            
        open_time_str = self._time_manager.market_open_time_str
        open_hour, open_minute = map(int, open_time_str.split(":"))
        next_open_date = datetime.strptime(next_open_str, "%Y%m%d")
        
        return self._time_manager.market_timezone.localize(
            datetime(next_open_date.year, next_open_date.month, next_open_date.day, open_hour, open_minute)
        )

    async def wait_until_next_open(self):
        """다음 개장 시간까지 스케줄러를 비동기적으로 대기시킵니다."""
        now = self._time_manager.get_current_kst_time()
        next_open = await self.get_next_open_time()
        
        seconds_left = max(0.0, (next_open - now).total_seconds())
        if seconds_left > 0:
            self._logger.info(f"다음 개장시간({next_open.strftime('%Y-%m-%d %H:%M:%S')})까지 {seconds_left:.1f}초 대기합니다. 💤")
            await asyncio.sleep(seconds_left)

    async def get_latest_market_close_time(self) -> Optional[datetime]:
        """
        가장 최근에 장이 마감된 정확한 '시간(datetime)'을 반환합니다.
        (예: 월요일 오전 10시라면 -> 지난주 금요일 15:30 반환)
        """
        now = self._time_manager.get_current_kst_time()
        today_str = now.strftime("%Y%m%d")

        # 1. 오늘이 영업일이고, 현재 시간이 이미 오늘 장 마감(15:30) 이후라면? -> 오늘 15:30
        if await self.is_business_day(today_str) and now >= self._time_manager.get_market_close_time():
            latest_close_str = today_str
        else:
            # 2. 휴장일이거나, 아직 오늘 장이 안 끝났다면(장전/장중) -> 과거로 거슬러 올라감
            check_dt = now - timedelta(days=1)
            
            # 최대 15일 전까지 거슬러 올라가며 가장 최근 영업일을 찾음
            for _ in range(15):
                check_str = check_dt.strftime("%Y%m%d")
                if await self.is_business_day(check_str):
                    latest_close_str = check_str
                    break
                check_dt -= timedelta(days=1)
            else:
                self._logger.error("최근 15일 내에 영업일이 없습니다. (시스템 오류 의심)")
                return None

        # 찾아낸 영업일 문자열(latest_close_str)과 TimeManager의 마감 시간(15:30)을 결합
        close_time_str = self._time_manager.market_close_time_str
        close_hour, close_minute = map(int, close_time_str.split(":"))
        close_date = datetime.strptime(latest_close_str, "%Y%m%d")
        
        return self._time_manager.market_timezone.localize(
            datetime(close_date.year, close_date.month, close_date.day, close_hour, close_minute)
        )