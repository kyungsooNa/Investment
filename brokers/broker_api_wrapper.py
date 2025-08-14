# user_api/broker_api_wrapper.py

from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from market_data.stock_code_mapper import StockCodeMapper
from typing import Any, List
from common.types import ResCommonResponse
from core.cache.cache_wrapper import cache_wrap_client


class BrokerAPIWrapper:
    """
    범용 사용자용 API Wrapper 클래스.
    증권사별 구현체를 내부적으로 호출하여, 일관된 방식의 인터페이스를 제공.
    """

    def __init__(self, broker: str = "korea_investment", env=None, logger=None, time_manager=None,
                 cache_config=None):
        self._broker = broker
        self._logger = logger
        self._client = None
        self._stock_mapper = StockCodeMapper(logger=logger)

        if broker == "korea_investment":
            if env is None:
                raise ValueError("KoreaInvest API를 사용하려면 env 인스턴스가 필요합니다.")

            self._client = KoreaInvestApiClient(env, logger, time_manager)
            self._client = cache_wrap_client(
                self._client, logger, time_manager,
                lambda: "PAPER" if env.is_paper_trading else "REAL",
                config=cache_config  # ✅ 여기서 주입
            )

        else:
            raise NotImplementedError(f"지원되지 않는 증권사: {broker}")

    # --- StockCodeMapper delegation ---
    async def get_name_by_code(self, code: str) -> str:
        """종목코드로 종목명을 반환합니다."""
        return self._stock_mapper.get_name_by_code(code)

    async def get_code_by_name(self, name: str) -> str:
        """종목명으로 종목코드를 반환합니다."""
        return self._stock_mapper.get_code_by_name(name)

    async def get_all_stock_codes(self) -> Any:
        """StockCodeMapper를 통해 모든 종목의 코드와 이름을 포함하는 DataFrame을 반환합니다."""
        if hasattr(self._stock_mapper, 'df'):
            return self._stock_mapper.df
        else:
            self._logger.error("StockCodeMapper가 초기화되지 않았거나 df 속성이 없습니다.")
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
    # async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
    #     """종목코드로 종목의 전체 정보를 가져옵니다 (KoreaInvestApiQuotations 위임)."""
    #     return await self._client.get_stock_info_by_code(stock_code)

    async def get_current_price(self, code: str) -> ResCommonResponse:
        """현재가를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_current_price(code)

    async def get_price_summary(self, code: str) -> ResCommonResponse:
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_price_summary(code)

    async def get_market_cap(self, code: str) -> ResCommonResponse:
        """종목코드로 시가총액을 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_market_cap(code)

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse:
        """시가총액 상위 종목 목록을 반환합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.get_top_market_cap_stocks_code(market_code, count)

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str,
                                           fid_period_div_code: str = 'D') -> ResCommonResponse:
        """일별/분봉 주식 시세 차트 데이터를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        return await self._client.inquire_daily_itemchartprice(stock_code, date,
                                                               fid_period_div_code=fid_period_div_code)

    async def get_asking_price(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 실시간 호가(매도/매수 잔량 포함) 정보를 조회합니다.
        """
        return await self._client.get_asking_price(stock_code)

    async def get_time_concluded_prices(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 시간대별 체결가/체결량 정보를 조회합니다.
        """
        return await self._client.get_time_concluded_prices(stock_code)

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

    # async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
    #     """
    #     외국인 순매수 상위 종목을 조회합니다.
    #     """
    #     return await self._client.get_top_foreign_buying_stocks()
    #
    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """
    #     특정 종목의 뉴스를 조회합니다.
    #     """
    #     return await self._client.get_stock_news(stock_code)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """
        특정 ETF의 상세 정보를 조회합니다.
        """
        return await self._client.get_etf_info(etf_code)

    # --- KoreaInvestApiClient / Account API delegation ---
    async def get_account_balance(self) -> ResCommonResponse:
        """계좌 잔고를 조회합니다 (KoreaInvestApiAccount 위임)."""
        return await self._client.get_account_balance()

    # --- KoreaInvestApiClient / Trading API delegation ---
    async def place_stock_order(self, stock_code, order_price, order_qty, is_buy: bool) -> ResCommonResponse:
        """범용 주식 주문을 실행합니다 (KoreaInvestApiTrading 위임)."""
        return await self._client.place_stock_order(stock_code, order_price, order_qty, is_buy)

    # --- KoreaInvestApiClient / WebSocket API delegation ---
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

    async def subscribe_realtime_quote(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 호가 데이터 구독합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code: str) -> Any:  # 실제 반환 값에 따라 타입 변경
        """실시간 호가 데이터 구독 해지합니다 (KoreaInvestWebSocketAPI 위임)."""
        return await self._client.unsubscribe_realtime_quote(stock_code)
