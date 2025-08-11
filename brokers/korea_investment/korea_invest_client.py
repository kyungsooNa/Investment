# brokers/korea_investment/korea_invest_client.py
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI
from brokers.korea_investment.korea_invest_header_provider import build_header_provider_from_env
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider

import certifi
import logging
import httpx  # 비동기 처리를 위해 requests 대신 httpx 사용
import ssl
from typing import Any
from common.types import ResCommonResponse


class KoreaInvestApiClient:
    """
    한국투자증권 Open API와 상호작용하는 메인 클라이언트입니다.
    각 도메인별 API 클래스를 통해 접근합니다.
    """

    def __init__(self, env: KoreaInvestApiEnv, logger=None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        shared_client = httpx.AsyncClient(verify=ssl_context)

        header_provider = build_header_provider_from_env(env)  # UA만 갖고 생성
        url_provider = KoreaInvestUrlProvider.from_env_and_kis_config(env=env)

        self._quotations = KoreaInvestApiQuotations(
            self._env,
            self._logger,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=url_provider,
        )
        self._account = KoreaInvestApiAccount(
            self._env,
            self._logger,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=url_provider,
        )
        self._trading = KoreaInvestApiTrading(
            self._env,
            self._logger,
            async_client=shared_client,
            header_provider=header_provider.fork(),
            url_provider=url_provider,
        )
        self._websocketAPI = KoreaInvestWebSocketAPI(self._env, self._logger)

    # --- Account API delegation ---
    async def get_account_balance(self) -> ResCommonResponse:
        return await self._account.get_account_balance()

    # --- Trading API delegation ---
    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool) -> ResCommonResponse:
        return await self._trading.place_stock_order(stock_code, order_price, order_qty, is_buy)

    # --- Quotations API delegation (Updated) ---
    # KoreaInvestApiQuotations의 모든 메서드가 ResCommonResponse를 반환하도록 이미 수정되었으므로, 해당 반환 타입을 반영
    async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
        """종목코드로 종목의 전체 정보를 가져옵니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_stock_info_by_code(stock_code)

    async def get_current_price(self, code: str) -> ResCommonResponse:
        """현재가를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_current_price(code)

    async def get_price_summary(self, code: str) -> ResCommonResponse:  # 반환 타입 변경
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_price_summary(code)

    async def get_market_cap(self, code: str) -> ResCommonResponse:  # 반환 타입 변경
        """종목코드로 시가총액을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_market_cap(code)

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse:  # 반환 타입 변경
        """시가총액 상위 종목 목록을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_top_market_cap_stocks_code(market_code, count)

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str,
                                           fid_period_div_code: str = 'D') -> ResCommonResponse:  # 반환 타입 변경
        """일별/분별 주식 시세 차트 데이터를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.inquire_daily_itemchartprice(stock_code, date,
                                                                   fid_period_div_code=fid_period_div_code)

    async def get_asking_price(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 실시간 호가(매도/매수 잔량 포함) 정보를 조회합니다.
        """
        return await self._quotations.get_asking_price(stock_code)

    async def get_time_concluded_prices(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 시간대별 체결가/체결량 정보를 조회합니다.
        """
        return await self._quotations.get_time_concluded_prices(stock_code)

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

    # async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
    #     """
    #     외국인 순매수 상위 종목을 조회합니다.
    #     """
    #     return await self._quotations.get_top_foreign_buying_stocks()
    #
    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """
    #     특정 종목의 뉴스를 조회합니다.
    #     """
    #     return await self._quotations.get_stock_news(stock_code)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """
        특정 ETF의 상세 정보를 조회합니다.
        """
        return await self._quotations.get_etf_info(etf_code)

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

    async def subscribe_realtime_quote(self, stock_code) -> Any:
        """실시간 주식호가 데이터를 구독합니다."""
        return await self._websocketAPI.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code) -> Any:
        """실시간 주식호가 데이터 구독을 해지합니다."""
        return await self._websocketAPI.unsubscribe_realtime_quote(stock_code)
