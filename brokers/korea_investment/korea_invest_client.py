# brokers/korea_investment/korea_invest_client.py
from typing import Optional, TYPE_CHECKING

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI
from brokers.korea_investment.korea_invest_header_provider import build_header_provider_from_env
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider

import certifi
import logging
import httpx  # 비동기 처리를 위해 requests 대신 httpx 사용
import ssl
from typing import Any
from common.types import ResCommonResponse, Exchange
from services.market_calendar_service import MarketCalendarService

if TYPE_CHECKING:
    from core.logger import StreamingEventLogger


class KoreaInvestApiClient:
    """
    한국투자증권 Open API와 상호작용하는 메인 클라이언트입니다.
    각 도메인별 API 클래스를 통해 접근합니다.
    """

    def __init__(self, env: KoreaInvestApiEnv, logger=None, market_clock=None,
                 market_calendar_service: Optional[MarketCalendarService] = None,
                 streaming_logger: Optional["StreamingEventLogger"] = None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        self.market_clock = market_clock
        self._mcs = market_calendar_service  # MarketCalendar는 나중에 set_market_calendar_service()로 주입받음

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=30.0)
        timeout = httpx.Timeout(10.0, connect=5.0)  # connect 5s, read/write/pool 10s
        shared_client = httpx.AsyncClient(verify=ssl_context, limits=limits, timeout=timeout)

        header_provider = build_header_provider_from_env(env)  # UA만 갖고 생성
        url_provider = KoreaInvestUrlProvider.from_env_and_kis_config(env=env)
        trid_provider = KoreaInvestTrIdProvider.from_config_loader(env=env)

        # 조회 API 전용: 항상 실전 URL 사용
        quotation_url_provider = KoreaInvestUrlProvider.from_env_and_kis_config(
            env=env, get_base_url_override=env.get_real_base_url
        )

        self._quotations = KoreaInvestApiQuotations(
            self._env,
            self._logger,
            self.market_clock,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=quotation_url_provider,
            trid_provider=trid_provider,
        )
        self._quotations._use_real_auth = True  # 항상 실전 인증
        self._account = KoreaInvestApiAccount(
            self._env,
            self._logger,
            self.market_clock,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=url_provider,
            trid_provider=trid_provider,
        )
        self._trading = KoreaInvestApiTrading(
            self._env,
            self._logger,
            self.market_clock,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=url_provider,
            trid_provider=trid_provider,
        )
        self._websocketAPI = KoreaInvestWebSocketAPI(
            self._env, self._logger,
            market_clock=self.market_clock,
            market_calendar_service=self._mcs,
            streaming_logger=streaming_logger,
        )

    # --- Account API delegation ---
    async def get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._account.get_account_balance(exchange=exchange)

    async def inquire_daily_ccld(self, **kwargs) -> ResCommonResponse:
        return await self._account.inquire_daily_ccld(**kwargs)

    # --- Trading API delegation ---
    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool,
                                exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        return await self._trading.place_stock_order(stock_code, order_price, order_qty, is_buy, exchange=exchange)

    # --- Quotations API delegation (Updated) ---
    # KoreaInvestApiQuotations의 모든 메서드가 ResCommonResponse를 반환하도록 이미 수정되었으므로, 해당 반환 타입을 반영
    async def get_stock_info_by_code(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """종목코드로 종목의 전체 정보를 가져옵니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_stock_info_by_code(stock_code, exchange=exchange)

    async def get_current_price(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """현재가를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_current_price(code, exchange=exchange)

    async def get_stock_conclusion(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """주식 체결(체결강도) 정보를 조회합니다."""
        return await self._quotations.get_stock_conclusion(code, exchange=exchange)

    async def get_price_summary(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_price_summary(code, exchange=exchange)

    async def get_market_cap(self, code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """종목코드로 시가총액을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_market_cap(code, exchange=exchange)

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse:
        """시가총액 상위 종목 목록을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_top_market_cap_stocks_code(market_code, count)

    async def inquire_daily_itemchartprice(self, stock_code: str, start_date: str, end_date: str,
                                           fid_period_div_code: str = 'D',
                                           exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """일별/분별 주식 시세 차트 데이터를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.inquire_daily_itemchartprice(stock_code, start_date=start_date, end_date=end_date,
                                                                   fid_period_div_code=fid_period_div_code,
                                                                   exchange=exchange)

    async def inquire_time_itemchartprice(
        self,
        *,
        stock_code: str,
        input_hour_1: str,
        pw_data_incu_yn: str = "Y",
        etc_cls_code: str = "0",
    ) -> ResCommonResponse:
        """
        당일 분봉 조회
        URL : /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
        TRID: FHKST03010200 (모의/실전 공통)
        """
        return await self._quotations.inquire_time_itemchartprice(
            stock_code=stock_code,
            input_hour=input_hour_1,
            include_past=pw_data_incu_yn,
            etc_cls_code=etc_cls_code)

    async def inquire_time_dailychartprice(
        self,
        *,
        stock_code: str,
        input_date_1: str,          # "YYYYMMDD"
        input_hour_1: str = "",     # 옵션(길이 10 권장)
        pw_data_incu_yn: str = "Y",
        fake_tick_incu_yn: str = "",  # 허봉 포함 여부: 공백 필수
    ) -> ResCommonResponse:
        """
        일별(특정 일자) 분봉 조회
        URL : /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice
        TRID: FHKST03010230 (모의투자 미지원)
        """
        return await self._quotations.inquire_time_dailychartprice(
            stock_code=stock_code,
            input_hour=input_hour_1,
            input_date=input_date_1,
            include_past=pw_data_incu_yn,
            fid_pw_data_incu_yn=fake_tick_incu_yn)

    async def get_asking_price(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        종목의 실시간 호가(매도/매수 잔량 포함) 정보를 조회합니다.
        """
        return await self._quotations.get_asking_price(stock_code, exchange=exchange)

    async def get_time_concluded_prices(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        종목의 시간대별 체결가/체결량 정보를 조회합니다.
        """
        return await self._quotations.get_time_concluded_prices(stock_code, exchange=exchange)

    # async def search_stocks_by_keyword(self, keyword: str) -> ResCommonResponse:
    #     """
    #     키워드로 종목을 검색합니다.
    #     """
    #     return await self._quotations.search_stocks_by_keyword(keyword)

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """
        상승률 또는 하락률 상위 종목을 조회합니다.

        Args:
            rise (bool): True이면 상승률, False이면 하락률 상위를 조회합니다.
        """
        return await self._quotations.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """
        거래량 상위 종목을 조회합니다.
        """
        return await self._quotations.get_top_volume_stocks()

    async def get_investor_trade_by_stock_daily(self, stock_code: str, date: str = None) -> ResCommonResponse:
        """종목별 투자자 매매동향(일별) 조회 (실전 전용)"""
        return await self._quotations.get_investor_trade_by_stock_daily(stock_code, date)

    async def get_investor_trade_by_stock_daily_multi(self, stock_code: str, date: str = None, days: int = 3) -> ResCommonResponse:
        """종목별 투자자 매매동향(일별) 다중일 조회 (실전 전용) — output2[:days] 리스트 반환"""
        return await self._quotations.get_investor_trade_by_stock_daily_multi(stock_code, date, days)

    async def get_program_trade_by_stock_daily(self, stock_code: str, date: str = None) -> ResCommonResponse:
        """종목별 프로그램 매매동향(일별) 조회 (실전 전용)"""
        return await self._quotations.get_program_trade_by_stock_daily(stock_code, date)
    
    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """
    #     특정 종목의 뉴스를 조회합니다.
    #     """
    #     return await self._quotations.get_stock_news(stock_code)

    async def get_multi_price(self, stock_codes: list[str]) -> ResCommonResponse:
        """복수종목 현재가를 조회합니다 (최대 30종목). ResCommonResponse를 반환합니다."""
        return await self._quotations.get_multi_price(stock_codes)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """
        특정 ETF의 상세 정보를 조회합니다.
        """
        return await self._quotations.get_etf_info(etf_code)

    async def get_financial_ratio(self, stock_code: str) -> ResCommonResponse:
        """기업 재무비율을 조회합니다 (영업이익 증가율 등)."""
        return await self._quotations.get_financial_ratio(stock_code)

    async def check_holiday(self, date: str) -> ResCommonResponse:
        """국내 휴장일 조회"""
        return await self._quotations.check_holiday(date)

    # --- WebSocket API delegation ---
    # 웹소켓 API는 연결/구독 성공 여부만 반환할 수 있으므로, ResCommonResponse로 래핑 여부는 구현에 따라 달라집니다.
    # 여기서는 임시로 Any로 두지만, ResCommonResponse(rt_cd, msg1, data=True/False) 형태로 변경하는 것을 고려할 수 있습니다.
    async def connect_websocket(self, on_message_callback=None) -> Any:
        """웹소켓 연결을 시작하고 실시간 데이터 수신을 준비합니다."""
        return await self._websocketAPI.connect(on_message_callback)

    async def disconnect_websocket(self) -> Any:
        """웹소켓 연결을 종료합니다."""
        return await self._websocketAPI.disconnect()

    async def subscribe_realtime_price(self, stock_code) -> Any:
        """실시간 주식체결 데이터(현재가)를 구독합니다."""
        return await self._websocketAPI.subscribe_realtime_price(stock_code)

    async def unsubscribe_realtime_price(self, stock_code) -> Any:
        """실시간 주식체결 데이터(현재가) 구독을 해지합니다."""
        return await self._websocketAPI.unsubscribe_realtime_price(stock_code)

    async def subscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독합니다 (KRX+NXT 통합)."""
        return await self._websocketAPI.subscribe_unified_price(stock_code)

    async def unsubscribe_unified_price(self, stock_code: str) -> bool:
        """실시간 통합 체결가(H0UNCNT0) 구독을 해지합니다."""
        return await self._websocketAPI.unsubscribe_unified_price(stock_code)

    async def subscribe_realtime_quote(self, stock_code) -> Any:
        """실시간 주식호가 데이터를 구독합니다."""
        return await self._websocketAPI.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code) -> Any:
        """실시간 주식호가 데이터 구독을 해지합니다."""
        return await self._websocketAPI.unsubscribe_realtime_quote(stock_code)

    async def subscribe_order_notice(self) -> Any:
        """국내주식 체결통보를 구독합니다."""
        return await self._websocketAPI.subscribe_order_notice()

    async def unsubscribe_order_notice(self) -> Any:
        """국내주식 체결통보 구독을 해지합니다."""
        return await self._websocketAPI.unsubscribe_order_notice()

    def is_websocket_receive_alive(self) -> bool:
        """웹소켓 수신 태스크가 살아있는지 확인."""
        return self._websocketAPI.is_receive_alive()

    async def subscribe_program_trading(self, stock_code: str):
        return await self._websocketAPI.subscribe_program_trading(stock_code)

    async def unsubscribe_program_trading(self, stock_code: str):
        return await self._websocketAPI.unsubscribe_program_trading(stock_code)
