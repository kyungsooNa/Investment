# services/trading_service.py
from api.client import KoreaInvestAPI
from api.env import KoreaInvestEnv
from core.time_manager import TimeManager  # TimeManager 임포트
import logging


class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, api_client: KoreaInvestAPI, env: KoreaInvestEnv, logger=None,
                 time_manager: TimeManager = None):  # time_manager 인자 추가
        self._api_client = api_client
        self._env = env
        self.logger = logger if logger else logging.getLogger(__name__)
        self._time_manager = time_manager  # TimeManager 인스턴스 저장

    def get_current_stock_price(self, stock_code):
        """주식 현재가를 조회하고 결과를 반환합니다."""
        self.logger.info(f"Service - {stock_code} 현재가 조회 요청")
        return self._api_client.quotations.get_current_price(stock_code)

    def get_account_balance(self):
        """계좌 잔고를 조회하고 결과를 반환합니다 (실전/모의 구분)."""
        self.logger.info(f"Service - 계좌 잔고 조회 요청 (환경: {'모의투자' if self._env.is_paper_trading else '실전'})")
        if self._env.is_paper_trading:
            return self._api_client.account.get_account_balance()
        else:
            return self._api_client.account.get_real_account_balance()

    def place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문을 제출하고 결과를 반환합니다."""
        self.logger.info(f"Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매수",
            order_dvsn
        )

    def place_sell_order(self, stock_code, price, qty, order_dvsn):
        """주식 매도 주문을 제출하고 결과를 반환합니다."""
        self.logger.info(f"Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매도",
            order_dvsn
        )

    def get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목을 조회하고 결과를 반환합니다 (모의투자 미지원)."""
        self.logger.info(f"Service - 시가총액 상위 종목 조회 요청 - 시장: {market_code}")
        if self._env.is_paper_trading:
            self.logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}

        return self._api_client.quotations.get_top_market_cap_stocks(market_code)

    def get_top_10_market_cap_stocks_with_prices(self):
        """
        시가총액 1~10위 종목의 현재가를 조회합니다.
        시장 개장 여부를 확인하고, 모의투자 미지원 API입니다.
        시장 개장까지 대기하며, 한 번 성공하면 결과를 반환합니다.
        """
        self.logger.info("Service - 시가총액 1~10위 종목 현재가 조회 요청")

        # 1. 시장 개장까지 대기
        if self._time_manager:
            while not self._time_manager.is_market_open():
                self.logger.warning("시장 닫힘: 시가총액 1~10위 종목 현재가 조회를 위해 시장이 열리기를 기다립니다 (60초 후 재확인).")
                self._time_manager.sleep(60)  # 60초 대기 후 재확인
            self.logger.info("시장이 열렸습니다. 시가총액 1~10위 종목 현재가 조회를 시작합니다.")
        else:
            self.logger.warning("TimeManager가 설정되지 않아 시장 개장 여부를 확인할 수 없습니다. 조회를 시도합니다.")

        # 2. 모의투자 미지원 API 경고
        if self._env.is_paper_trading:
            self.logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}  # 모의투자에서는 이 기능을 사용할 수 없음

        # 3. 시가총액 상위 종목 목록 조회 (전체 시장 0000)
        top_stocks_response = self.get_top_market_cap_stocks("0000")

        if not top_stocks_response or top_stocks_response.get('rt_cd') != '0':
            self.logger.error(f"시가총액 상위 종목 조회 실패: {top_stocks_response}")
            return None

        top_stocks_list = top_stocks_response.get('output', [])
        if not top_stocks_list:
            self.logger.info("시가총액 상위 종목 목록을 찾을 수 없습니다.")
            return None

        # 4. 상위 10개 종목에 대해 현재가 조회
        results = []
        for i, stock_info in enumerate(top_stocks_list):
            if i >= 10:  # 상위 10개만 처리
                break

            stock_code = stock_info.get('mksc_shrn_iscd')
            stock_name = stock_info.get('hts_kor_isnm')
            stock_rank = stock_info.get('data_rank')

            if stock_code:
                current_price_response = self.get_current_stock_price(stock_code)
                if current_price_response and current_price_response.get('rt_cd') == '0':
                    current_price = current_price_response['output'].get('stck_prpr', 'N/A')
                    results.append({
                        'rank': stock_rank,
                        'name': stock_name,
                        'code': stock_code,
                        'current_price': current_price
                    })
                    self.logger.debug(f"종목 {stock_code} ({stock_name}) 현재가 {current_price} 조회 성공.")
                else:
                    self.logger.error(f"종목 {stock_code} ({stock_name}) 현재가 조회 실패: {current_price_response}")
            else:
                self.logger.warning(f"시가총액 상위 종목 목록에서 유효한 종목코드를 찾을 수 없습니다: {stock_info}")

        if results:  # 결과가 하나라도 있으면 성공으로 간주
            self.logger.info("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")
            return results
        else:
            self.logger.warning("시가총액 1~10위 종목 현재가 조회 결과 없음.")
            return None
