# managers/market_date_manager.py
from typing import Optional, Any
import logging
from datetime import datetime, timedelta
from core.time_manager import TimeManager

class MarketDateManager:
    """
    최근 거래일(개장일) 정보를 관리하고 캐싱하는 매니저.
    하루에 한 번만 API를 호출하여 확인하고 메모리에 캐시합니다.
    """
    def __init__(self, time_manager: TimeManager, logger: Optional[logging.Logger] = None):
        self._time_manager = time_manager
        self._logger = logger or logging.getLogger(__name__)
        self._broker: Optional[Any] = None  # BrokerAPIWrapper
        self._cached_date: Optional[str] = None
        self._last_check_date: Optional[str] = None

    def set_broker(self, broker: Any):
        """BrokerAPIWrapper 인스턴스 주입"""
        self._broker = broker

    async def get_latest_trading_date(self) -> Optional[str]:
        """
        가장 최근 거래일(YYYYMMDD)을 반환.
        오늘 이미 확인했다면 캐시된 값을 반환합니다.
        """
        today = self._time_manager.get_current_kst_time().strftime("%Y%m%d")
        
        # 오늘 이미 확인했다면 캐시 반환
        if self._cached_date and self._last_check_date == today:
            return self._cached_date
        
        if not self._broker:
            self._logger.warning("MarketDateManager: Broker is not set.")
            return self._cached_date

        try:
            date = await self._fetch_from_api()
            if date:
                self._cached_date = date
                self._last_check_date = today
                self._logger.info(f"MarketDateManager: Latest trading date updated to {date}")
        except Exception as e:
            self._logger.error(f"MarketDateManager: Failed to fetch latest trading date: {e}")
            
        return self._cached_date

    async def _fetch_from_api(self) -> Optional[str]:
        """실제 API를 호출하여 최근 거래일을 확인 (캐시 재귀 호출 방지 로직 포함)"""
        try:
            # Broker -> Client -> Quotations -> Raw Client (ClientWithCache 우회)
            # 구조: broker._client (KoreaInvestApiClient) -> _quotations (ClientWithCache) -> _client (KoreaInvestApiQuotations)
            raw_quotations = None
            
            # 안전하게 속성 접근
            kis_client = getattr(self._broker, '_client', None)
            if kis_client:
                quotations = getattr(kis_client, '_quotations', None)
                if quotations:
                    # ClientWithCache로 래핑되어 있다면 내부 _client 사용
                    raw_quotations = getattr(quotations, '_client', quotations)
            
            if not raw_quotations:
                self._logger.error("MarketDateManager: Failed to retrieve raw quotations client. Broker structure might have changed.")
                return None

            now = self._time_manager.get_current_kst_time()
            end_dt = now.strftime("%Y%m%d")
            start_dt = (now - timedelta(days=7)).strftime("%Y%m%d")
            
            # 삼성전자(005930) 일봉 조회 (Raw API 호출)
            resp = await raw_quotations.inquire_daily_itemchartprice("005930", start_dt, end_dt, "D")
            
            if resp and resp.rt_cd == "0" and resp.data:
                dates = []
                for d in resp.data:
                    # ResDailyChartApiItem 객체 또는 dict 지원
                    date_val = getattr(d, "stck_bsop_date", None)
                    if date_val is None and isinstance(d, dict):
                        date_val = d.get("stck_bsop_date")
                    
                    if date_val:
                        dates.append(date_val)
                        
                if dates:
                    return max(dates)
            else:
                msg = getattr(resp, 'msg1', 'Unknown Error')
                self._logger.warning(f"MarketDateManager: API call failed or no data. msg={msg}")

        except Exception as e:
            self._logger.warning(f"최근 거래일 조회 실패 (API): {e}", exc_info=True)
        return None
