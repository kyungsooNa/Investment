# user_api/broker_api_wrapper.py

from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from repositories.stock_code_repository import StockCodeRepository
from typing import Any, List, Optional, TYPE_CHECKING
from common.types import ResCommonResponse, Exchange, ErrorCode
from core.cache.cache_wrapper import cache_wrap_client
from core.retry_queue.api_request_queue import ApiRequestQueue
from core.retry_queue.client_with_retry_queue import retry_queue_wrap_client
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from core.logger import StreamingEventLogger


class BrokerAPIWrapper:
    """
    범용 사용자용 API Wrapper 클래스.
    증권사별 구현체를 내부적으로 호출하여, 일관된 방식의 인터페이스를 제공.
    """

    # 서킷 브레이커 기본값
    _CB_THRESHOLD = 5        # 연속 실패 N회 시 개방
    _CB_TIMEOUT_MIN = 5      # 개방 후 M분 동안 차단

    def __init__(self, broker: str = "korea_investment", env=None, logger=None, market_clock=None,
                 cache_config=None, market_calendar_service=None,
                 streaming_logger: Optional["StreamingEventLogger"] = None,
                 stock_code_repository=None,
                 circuit_breaker_threshold: int = _CB_THRESHOLD,
                 circuit_breaker_timeout_min: int = _CB_TIMEOUT_MIN):
        self._broker = broker
        self._logger = logger
        self._client = None
        self._stock_mapper = stock_code_repository if stock_code_repository is not None else StockCodeRepository(logger=logger)
        self.env = env
        self._retry_queue: ApiRequestQueue | None = None

        # 서킷 브레이커 상태
        self._cb_threshold = circuit_breaker_threshold
        self._cb_timeout_min = circuit_breaker_timeout_min
        self._cb_consecutive_failures: int = 0
        self._cb_open_until: Optional[datetime] = None

        if broker == "korea_investment":
            if env is None:
                raise ValueError("KoreaInvest API를 사용하려면 env 인스턴스가 필요합니다.")

            self._client = KoreaInvestApiClient(
                env, logger, market_clock, market_calendar_service,
                streaming_logger=streaming_logger,
            )
            # RetryQueue는 Cache 안쪽에 위치: 캐시 히트 시 Queue를 거치지 않고,
            # 캐시 miss 후 실제 API 호출 실패 시에만 KoreaInvestApiClient를 직접 재시도
            self._retry_queue = ApiRequestQueue(logger=logger)
            self._client = retry_queue_wrap_client(self._client, self._retry_queue)
            self._client = cache_wrap_client(

                self._client, logger, market_clock,
                lambda: "PAPER" if env.is_paper_trading else "REAL",

                config=cache_config,
                market_calendar_service=market_calendar_service
            )

        else:
            raise NotImplementedError(f"지원되지 않는 증권사: {broker}")

    async def stop(self):
        """이벤트 루프 종료 전 대기 중인 재시도 태스크를 정리합니다."""
        if self._retry_queue:
            await self._retry_queue.stop()

    # --- StockCodeRepository delegation ---
    async def get_name_by_code(self, code: str) -> str:
        """종목코드로 종목명을 반환합니다."""
        return self._stock_mapper.get_name_by_code(code)

    async def get_code_by_name(self, name: str) -> str:
        """종목명으로 종목코드를 반환합니다."""
        return self._stock_mapper.get_code_by_name(name)

    async def get_all_stock_codes(self) -> Any:
        """StockCodeRepository를 통해 모든 종목의 코드와 이름을 포함하는 DataFrame을 반환합니다."""
        if hasattr(self._stock_mapper, 'df'):
            return self._stock_mapper.df
        else:
            self._logger.error("StockCodeRepository가 초기화되지 않았거나 df 속성이 없습니다.")
            return None

    async def get_all_stock_code_list(self) -> List[str]:
        """모든 종목 코드 리스트만 반환합니다."""
        df = await self.get_all_stock_codes()
        if df is not None and '종목코드' in df.columns:
            return df['종목코드'].tolist()
        return []

    async def get_all_stock_name_list(self) -> List[str]:
        """모든 종목명 리스트만 반환합니다."""
        df = await self.get_all_stock_codes()
        if df is not None and '종목명' in df.columns:
            return df['종목명'].tolist()
        return []

    # --- KoreaInvestApiClient / Quotations API delegation ---
    async def get_stock_info_by_code(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """종목코드로 종목의 전체 정보를 가져옵니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_stock_info_by_code(stock_code, exchange=exchange)

    async def get_current_price(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """현재가를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_current_price(code, exchange=exchange)

    async def get_stock_conclusion(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """주식 체결(체결강도) 정보를 조회합니다."""
        return await self._client.get_stock_conclusion(code, exchange=exchange)

    async def get_price_summary(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_price_summary(code, exchange=exchange)

    async def get_market_cap(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """종목코드로 시가총액을 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_market_cap(code, exchange=exchange)

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse:
        """시가총액 상위 종목 목록을 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_top_market_cap_stocks_code(market_code, count)

    async def inquire_daily_itemchartprice(self, stock_code: str, start_date: str, end_date: str,
                                           fid_period_div_code: str = 'D',
                                           exchange: Exchange = Exchange.KRX, **kwargs) -> ResCommonResponse:
        """일별/분봉 주식 시세 차트 데이터를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.inquire_daily_itemchartprice(stock_code, start_date=start_date, end_date=end_date,
                                                               fid_period_div_code=fid_period_div_code,
                                                               exchange=exchange, **kwargs)
    async def inquire_time_itemchartprice(
        self, *, stock_code: str, input_hour_1: str,
        pw_data_incu_yn: str = "Y", etc_cls_code: str = "0"
    ) -> ResCommonResponse:
        return await self._client.inquire_time_itemchartprice(
            stock_code=stock_code,
            input_hour_1=input_hour_1,
            pw_data_incu_yn=pw_data_incu_yn,
            etc_cls_code=etc_cls_code,
        )

    async def inquire_time_dailychartprice(
        self, *, stock_code: str, input_date_1: str, input_hour_1: str = "",
        pw_data_incu_yn: str = "Y", fake_tick_incu_yn: str = ""
    ) -> ResCommonResponse:
        return await self._client.inquire_time_dailychartprice(
            stock_code=stock_code,
            input_date_1=input_date_1,
            input_hour_1=input_hour_1,
            pw_data_incu_yn=pw_data_incu_yn,
            fake_tick_incu_yn=fake_tick_incu_yn,
        )

    async def get_asking_price(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        종목의 실시간 호가(매도/매수 잔량 포함) 정보를 조회합니다.
        """
        return await self._client.get_asking_price(stock_code, exchange=exchange)

    async def get_time_concluded_prices(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        종목의 시간대별 체결가/체결량 정보를 조회합니다.
        """
        return await self._client.get_time_concluded_prices(stock_code, exchange=exchange)

    # async def search_stocks_by_keyword(self, keyword: str) -> ResCommonResponse:
    #     """
    #     키워드로 종목을 검색합니다.
    #     """
    #     return await self._client.search_stocks_by_keyword(keyword)

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """
        상승률 또는 하락률 상위 종목을 조회합니다.

        Args:
            rise (bool): True이면 상승률, False이면 하락률 상위를 조회합니다.
        """
        return await self._client.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """
        거래량 상위 종목을 조회합니다.
        """
        return await self._client.get_top_volume_stocks()

    async def get_investor_trade_by_stock_daily(self, stock_code: str, date: str = None) -> ResCommonResponse:
        """종목별 투자자 매매동향(일별) 조회 (실전 전용)"""
        return await self._client.get_investor_trade_by_stock_daily(stock_code, date)

    async def get_investor_trade_by_stock_daily_multi(self, stock_code: str, date: str = None, days: int = 3) -> ResCommonResponse:
        """종목별 투자자 매매동향(일별) 다중일 조회 (실전 전용) — output2[:days] 리스트 반환"""
        return await self._client.get_investor_trade_by_stock_daily_multi(stock_code, date, days)

    async def get_program_trade_by_stock_daily(self, stock_code: str, date: str = None) -> ResCommonResponse:
        """종목별 프로그램매매추이(일별) 조회 (실전 전용)"""
        return await self._client.get_program_trade_by_stock_daily(stock_code, date)
    #
    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """
    #     특정 종목의 뉴스를 조회합니다.
    #     """
    #     return await self._client.get_stock_news(stock_code)

    async def get_multi_price(self, stock_codes: list[str]) -> ResCommonResponse:
        """복수종목 현재가를 조회합니다 (최대 30종목, KoreaInvestApiQuotations 위임)."""
        return await self._client.get_multi_price(stock_codes)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """
        특정 ETF의 상세 정보를 조회합니다.
        """
        return await self._client.get_etf_info(etf_code)

    async def get_financial_ratio(self, stock_code: str) -> ResCommonResponse:
        """기업 재무비율을 조회합니다 (영업이익 증가율 등)."""
        return await self._client.get_financial_ratio(stock_code)
    
    async def check_holiday(self, date: str) -> ResCommonResponse:
        """국내 휴장일 조회"""
        return await self._client.check_holiday(date)
    
    # --- KoreaInvestApiClient / Account API delegation ---
    async def get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """계좌 잔고를 조회합니다 (KoreaInvestApiAccount 위임)."""
        return await self._client.get_account_balance(exchange=exchange)

    # --- 서킷 브레이커 ---

    def _cb_is_open(self) -> bool:
        """서킷이 개방(차단) 상태인지 반환한다."""
        if self._cb_open_until is None:
            return False
        if datetime.now() >= self._cb_open_until:
            self._cb_open_until = None
            self._cb_consecutive_failures = 0
            if self._logger:
                self._logger.info("[CircuitBreaker] 차단 해제 — 정상 운영 재개")
            return False
        return True

    def _cb_record_failure(self):
        self._cb_consecutive_failures += 1
        if self._cb_consecutive_failures >= self._cb_threshold:
            self._cb_open_until = datetime.now() + timedelta(minutes=self._cb_timeout_min)
            if self._logger:
                self._logger.error(
                    f"[CircuitBreaker] 연속 {self._cb_consecutive_failures}회 실패 → "
                    f"{self._cb_timeout_min}분 차단 (해제: {self._cb_open_until.strftime('%H:%M:%S')})"
                )

    def _cb_record_success(self):
        if self._cb_consecutive_failures > 0:
            self._cb_consecutive_failures = 0

    # --- KoreaInvestApiClient / Trading API delegation ---
    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool,
                                exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """범용 주식 주문을 실행합니다 (KoreaInvestApiTrading 위임)."""
        if self._cb_is_open():
            remaining = (self._cb_open_until - datetime.now()).seconds // 60
            if self._logger:
                self._logger.warning(
                    f"[CircuitBreaker] 주문 차단됨 ({remaining}분 남음): {stock_code}"
                )
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=f"서킷 브레이커 개방 — {remaining}분 후 재시도",
            )
        resp = await self._client.place_stock_order(stock_code, order_price, order_qty, is_buy, exchange=exchange)
        rt_cd = getattr(resp, 'rt_cd', None) if resp else None
        if rt_cd == ErrorCode.SUCCESS.value:
            self._cb_record_success()
        else:
            self._cb_record_failure()
        return resp

    # --- KoreaInvestApiClient / WebSocket API delegation ---
    def is_websocket_receive_alive(self) -> bool:
        """웹소켓 수신 태스크가 살아있는지 확인."""
        return self._client.is_websocket_receive_alive()

    async def connect_websocket(self, on_message_callback=None) -> Any:  # 실제 반환 값에 따라 타입 변경
        """웹소켓 연결을 시작합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.connect_websocket(on_message_callback)

    async def disconnect_websocket(self) -> Any:  # 실제 반환 값에 따라 타입 변경
        """웹소켓 연결을 종료합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.disconnect_websocket()

    async def subscribe_realtime_price(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 체결 데이터 구독합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.subscribe_realtime_price(stock_code)

    async def unsubscribe_realtime_price(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 체결 데이터 구독 해지합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.unsubscribe_realtime_price(stock_code)

    async def subscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독합니다 (KRX+NXT 통합)."""
        return await self._client.subscribe_unified_price(stock_code)

    async def unsubscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독 해지합니다."""
        return await self._client.unsubscribe_unified_price(stock_code)

    async def subscribe_realtime_quote(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 호가 데이터 구독합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 호가 데이터 구독 해지합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.unsubscribe_realtime_quote(stock_code)

    async def subscribe_program_trading(self, stock_code: str):
        return await self._client.subscribe_program_trading(stock_code)

    async def unsubscribe_program_trading(self, stock_code: str):
        return await self._client.unsubscribe_program_trading(stock_code)