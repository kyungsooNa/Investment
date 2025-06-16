# services/trading_service.py
from api.client import KoreaInvestAPI
from api.env import KoreaInvestEnv  # 환경 정보를 얻기 위해 필요


class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, api_client: KoreaInvestAPI, env: KoreaInvestEnv):
        self._api_client = api_client
        self._env = env  # 환경 정보 (is_paper_trading 등)를 확인하기 위해 필요

    def get_current_stock_price(self, stock_code):
        """주식 현재가를 조회하고 결과를 반환합니다."""
        print(f"INFO: Service - {stock_code} 현재가 조회 요청")
        return self._api_client.quotations.get_current_price(stock_code)

    def get_account_balance(self):
        """계좌 잔고를 조회하고 결과를 반환합니다 (실전/모의 구분)."""
        print(f"INFO: Service - 계좌 잔고 조회 요청 (환경: {'모의투자' if self._env.is_paper_trading else '실전'})")
        if self._env.is_paper_trading:
            return self._api_client.account.get_account_balance()  # 모의투자용 호출
        else:
            return self._api_client.account.get_real_account_balance()  # 실전용 호출

    def place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문을 제출하고 결과를 반환합니다."""
        print(f"INFO: Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매수",
            order_dvsn
        )

    def place_sell_order(self, stock_code, price, qty, order_dvsn):
        """주식 매도 주문을 제출하고 결과를 반환합니다."""
        print(f"INFO: Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매도",
            order_dvsn
        )

    def get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목을 조회하고 결과를 반환합니다."""
        print(f"INFO: Service - 시가총액 상위 종목 조회 요청 - 시장: {market_code}")
        # 이 API는 모의투자를 지원하지 않으므로, 환경 확인 로직을 서비스 계층에서 포함.
        if self._env.is_paper_trading:
            print("WARNING: Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}  # 서비스 계층에서 오류 메시지 반환

        return self._api_client.quotations.get_top_market_cap_stocks(market_code)
