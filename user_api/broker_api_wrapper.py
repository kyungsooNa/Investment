# user_api/broker_api_wrapper.py

from brokers.korea_investment.korea_invest_account_api import KoreaInvestAccountAPI
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from market_data.stock_code_mapper import StockCodeMapper


class BrokerAPIWrapper:
    """
    범용 사용자용 API Wrapper 클래스.
    증권사별 구현체를 내부적으로 호출하여, 일관된 방식의 인터페이스를 제공.
    """
    def __init__(self, broker: str = "korea_investment", logger=None):
        self.broker = broker
        self.logger = logger

        if broker == "korea_investment":
            self.account = KoreaInvestAccountAPI(logger=logger)
            self.trading = KoreaInvestApiTrading(logger=logger)
            self.quotations = KoreaInvestApiQuotations(logger=logger)
        else:
            raise NotImplementedError(f"지원되지 않는 증권사: {broker}")

        self.stock_mapper = StockCodeMapper(logger=logger)

    def get_name_by_code(self, code: str) -> str:
        return self.stock_mapper.get_name_by_code(code)

    def get_code_by_name(self, name: str) -> str:
        return self.stock_mapper.get_code_by_name(name)

    async def get_balance(self):
        return await self.account.get_balance()

    async def buy_stock(self, code: str, quantity: int, price: int):
        return await self.trading.buy(code, quantity, price)

    async def sell_stock(self, code: str, quantity: int, price: int):
        return await self.trading.sell(code, quantity, price)

    async def get_price_summary(self, code: str):
        return await self.quotations.get_price_summary(code)

    async def get_market_cap(self, code: str):
        return await self.quotations.get_market_cap(code)
