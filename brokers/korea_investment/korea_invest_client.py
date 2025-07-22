# brokers/korea_investment/korea_invest_client.py
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_websocket_api import KoreaInvestWebSocketAPI
from brokers.korea_investment.korea_invest_token_manager import TokenManager # TokenManager를 import
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

    def __init__(self, env: KoreaInvestApiEnv, token_manager:TokenManager, logger=None):
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        self._token_manager = token_manager

        # _config는 env.get_full_config()를 통해 모든 설정(tr_ids 포함)을 가져옴
        self._config = self._env.get_full_config()

        # get_full_config가 access_token을 포함하도록 변경되었으므로 여기서 바로 확인
        if not self._config.get('access_token'):
            raise ValueError("접근 토큰이 없습니다. KoreaInvestEnv에서 먼저 토큰을 발급받아야 합니다.")

        # API 호출 시 사용할 기본 헤더 설정 (하위 클래스에 전달)
        # _env.get_base_headers()는 access_token 포함 여부만 판단하므로,
        # 여기서는 access_token이 확보된 후 _config에서 직접 값들을 가져와 헤더를 구성합니다.
        common_headers_template = {
            "Content-Type": "application/json",
            "User-Agent": self._env.my_agent,
            "charset": "UTF-8",
            "Authorization": f"Bearer {self._config['access_token']}",  # _config에서 access_token 사용
            "appkey": self._config['api_key'],  # _config에서 api_key 사용
            "appsecret": self._config['api_secret_key']  # _config에서 api_secret_key 사용
        }

        base_url = self._config['base_url']

        # 각 도메인별 API 클래스 인스턴스화
        # _config에서 바로 base_url을 가져와 전달
        shared_client = httpx.AsyncClient(verify=ssl.create_default_context(cafile=certifi.where()))

        self._quotations = KoreaInvestApiQuotations(base_url, common_headers_template, self._config,
                                                   self._token_manager,self._logger, async_client=shared_client)
        self._account = KoreaInvestApiAccount(base_url, common_headers_template, self._config,
                                             self._token_manager, self._logger, async_client=shared_client)
        self._trading = KoreaInvestApiTrading(base_url, common_headers_template, self._config,
                                             self._token_manager, self._logger, async_client=shared_client)
        self._websocketAPI = KoreaInvestWebSocketAPI(self._env, self._logger)


    # --- Account API delegation ---
    # KoreaInvestApiAccount의 get_account_balance, get_real_account_balance도 ResCommonResponse를 반환하도록 수정 필요
    async def get_account_balance(self) -> ResCommonResponse:
        return await self._account.get_account_balance()

    async def get_real_account_balance(self) -> ResCommonResponse:
        return await self._account.get_real_account_balance()

    # --- Trading API delegation ---
    # KoreaInvestApiTrading의 place_stock_order도 ResCommonResponse를 반환하도록 수정 필요
    async def buy_stock(self, stock_code: str, order_price, order_qty, trade_type, order_dvsn) -> ResCommonResponse:
        return await self._trading.place_stock_order(stock_code, order_price, order_qty, "buy", order_dvsn)

    async def sell_stock(self, stock_code: str, order_price, order_qty, trade_type, order_dvsn) -> ResCommonResponse:
        return await self._trading.place_stock_order(stock_code, order_price, order_qty, "sell", order_dvsn)

    async def place_stock_order(self, stock_code, order_price, order_qty, trade_type, order_dvsn) -> ResCommonResponse:
        return await self._trading.place_stock_order(stock_code, order_price, order_qty, trade_type, order_dvsn)


    # --- Quotations API delegation (Updated) ---
    # KoreaInvestApiQuotations의 모든 메서드가 ResCommonResponse를 반환하도록 이미 수정되었으므로, 해당 반환 타입을 반영
    async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
        """종목코드로 종목의 전체 정보를 가져옵니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_stock_info_by_code(stock_code)

    async def get_current_price(self, code: str) -> ResCommonResponse:
        """현재가를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_current_price(code)

    async def get_price_summary(self, code: str) -> ResCommonResponse: # 반환 타입 변경
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_price_summary(code)

    async def get_market_cap(self, code: str) -> ResCommonResponse: # 반환 타입 변경
        """종목코드로 시가총액을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_market_cap(code)

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse: # 반환 타입 변경
        """시가총액 상위 종목 목록을 반환합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_top_market_cap_stocks_code(market_code, count)

    def get_previous_day_info(self, code: str) -> ResCommonResponse: # 반환 타입 변경
        """종목의 전일 종가, 전일 거래량을 조회합니다. ResCommonResponse를 반환합니다."""
        return self._quotations.get_previous_day_info(code)

    async def get_filtered_stocks_by_momentum(
            self, count=20, min_change_rate=10.0, min_volume_ratio=2.0
    ) -> ResCommonResponse: # 반환 타입 변경
        """거래량 급증 + 등락률 조건 기반 모멘텀 종목 필터링합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.get_filtered_stocks_by_momentum(count, min_change_rate, min_volume_ratio)

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str, fid_period_div_code: str = 'D') -> ResCommonResponse: # 반환 타입 변경
        """일별/분별 주식 시세 차트 데이터를 조회합니다. ResCommonResponse를 반환합니다."""
        return await self._quotations.inquire_daily_itemchartprice(stock_code, date, fid_period_div_code=fid_period_div_code)

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

    async def search_stocks_by_keyword(self, keyword: str) -> ResCommonResponse:
        """
        키워드로 종목을 검색합니다.
        """
        return await self._quotations.search_stocks_by_keyword(keyword)

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

    async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
        """
        외국인 순매수 상위 종목을 조회합니다.
        """
        return await self._quotations.get_top_foreign_buying_stocks()

    async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
        """
        특정 종목의 뉴스를 조회합니다.
        """
        return await self._quotations.get_stock_news(stock_code)

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

    def __str__(self):
        """객체를 문자열로 표현할 때 사용."""
        class_name = self.__class__.__name__
        return f"{class_name}(base_url={self._config['base_url']}, is_paper_trading={self._config['is_paper_trading']})"
