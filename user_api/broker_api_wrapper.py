# user_api/broker_api_wrapper.py

from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from market_data.stock_code_mapper import StockCodeMapper


class BrokerAPIWrapper:
    """
    범용 사용자용 API Wrapper 클래스.
    증권사별 구현체를 내부적으로 호출하여, 일관된 방식의 인터페이스를 제공.
    """
    def __init__(self, broker: str = "korea_investment", env=None, token_manager=None, logger=None):
        self.broker = broker
        self.logger = logger
        self.token_manager = token_manager

        if broker == "korea_investment":
            if env is None:
                raise ValueError("KoreaInvest API를 사용하려면 env 인스턴스가 필요합니다.")

            config = env.get_full_config()
            base_url = config['base_url']
            headers = {
                "authorization": f"Bearer {config['access_token']}",
                "appkey": config['api_key'],
                "appsecret": config['api_secret_key'],
                "tr_id": "",
                "custtype": config['custtype']
            }

            self.account = KoreaInvestApiAccount(base_url, headers.copy(), config, token_manager, logger)
            self.trading = KoreaInvestApiTrading(base_url, headers.copy(), config, token_manager, logger)
            self.quotations = KoreaInvestApiQuotations(base_url, headers.copy(), config, token_manager, logger)
        else:
            raise NotImplementedError(f"지원되지 않는 증권사: {broker}")

        self.stock_mapper = StockCodeMapper(logger=logger)

    async def get_name_by_code(self, code: str) -> str:
        return self.stock_mapper.get_name_by_code(code)

    async def get_code_by_name(self, name: str) -> str:
        return self.stock_mapper.get_code_by_name(name)

    async def get_balance(self):
        return await self.account.get_account_balance()

    async def buy_stock(self, code: str, quantity: int, price: int):
        return await self.trading.buy(code, quantity, price)

    async def sell_stock(self, code: str, quantity: int, price: int):
        return await self.trading.sell(code, quantity, price)

    async def get_price_summary(self, code: str):
        return await self.quotations.get_price_summary(code)

    async def get_market_cap(self, code: str):
        return await self.quotations.get_market_cap(code)

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str, fid_period_div_code: str = 'D'):
        """
        일별/분봉 주식 시세 차트 데이터를 조회합니다.
        :param stock_code: 종목코드
        :param date: 조회 날짜 (YYYYMMDD)
        :param fid_period_div_code: 기간 구분 코드 (D:일봉, M:분봉 등)
        """
        # fid_period_div_code 인자를 self.quotations.inquire_daily_itemchartprice로 전달
        return await self.quotations.inquire_daily_itemchartprice(stock_code, date, fid_period_div_code=fid_period_div_code)
