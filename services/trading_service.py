# services/trading_service.py
from api.client import KoreaInvestAPI
from api.env import KoreaInvestEnv
from core.time_manager import TimeManager
import logging
import datetime  # datetime 임포트


class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, api_client: KoreaInvestAPI, env: KoreaInvestEnv, logger=None, time_manager: TimeManager = None):
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
        시장 개장까지 대기하며, 한 번 성공하면 결과를 반환하고 프로그램 종료를 유도합니다.
        """
        self.logger.info("Service - 시가총액 1~10위 종목 현재가 조회 요청")

        # 1. 시장 개장까지 스마트하게 대기
        if self._time_manager:
            # 시장이 열릴 때까지 반복 (최대 24시간 이내의 대기만 처리)
            # is_market_open()은 내부적으로 logger를 사용하고, 시장 상태를 출력함
            while not self._time_manager.is_market_open():
                next_open_time = self._time_manager.get_next_market_open_time()
                time_to_wait_timedelta = next_open_time - self._time_manager.get_current_kst_time()
                wait_seconds_total = int(time_to_wait_timedelta.total_seconds())

                if wait_seconds_total <= 0:
                    self.logger.info("시장 개장 예상 시간 도달. 시장 상태 재확인 중...")
                    if self._time_manager.is_market_open():
                        print("\r" + " " * 80 + "\r", end="")  # 마지막 대기 메시지 지우기
                        break  # 시장이 열렸으면 대기 루프 종료
                    else:
                        self.logger.warning("예상 개장 시간 이후에도 시장이 열리지 않았습니다. 5초 후 재확인.")
                        self._time_manager.sleep(5)
                        continue  # 루프를 다시 시작하여 시간 재계산

                if wait_seconds_total >= 24 * 3600:
                    self.logger.warning("시장이 닫혀 있으며, 다음 개장 시간이 24시간 초과입니다. 조회를 시도하지 않습니다.")
                    return None

                # 현재 시간의 초를 가져와 다음 00초까지 대기할 시간을 계산
                current_seconds = self._time_manager.get_current_kst_time().second
                seconds_to_next_minute = (60 - current_seconds) % 60
                if seconds_to_next_minute == 0:  # 이미 정각일 경우, 다음 분까지 60초 대기
                    seconds_to_next_minute = 60

                    # 남은 시간을 시, 분으로만 포맷팅 (초는 00으로 표시될 것이므로 생략)
                hours, remainder = divmod(wait_seconds_total, 3600)
                minutes, seconds_remaining_in_minute = divmod(remainder, 60)  # 초는 제외하고 분까지 정확히 계산

                # 초가 00일 때만 업데이트 메시지 출력 (혹은 초가 00이 아니더라도 분 단위로만 표기)
                formatted_time_to_wait = ""
                if hours > 0:
                    formatted_time_to_wait += f"{hours}시간 "
                formatted_time_to_wait += f"{minutes}분"

                # 사용자 콘솔 출력 (덮어쓰기 위해 \r 사용)
                message_to_display = f"시장 개장까지 대기 중... ({formatted_time_to_wait} 남음)"
                print(f"\r{message_to_display}{' ' * (80 - len(message_to_display))}", end="")
                self.logger.info(f"시장 닫힘: 대기 중... ({formatted_time_to_wait} 남음)")  # 로그는 계속 기록

                # 다음 정각(00초)까지 대기한 후, 다음 대기 주기는 60초로 설정
                self._time_manager.sleep(seconds_to_next_minute)

            print("\r" + " " * 80 + "\r", end="")  # 최종적으로 대기 메시지 지우기
            self.logger.info("시장이 열렸습니다. 시가총액 1~10위 종목 현재가 조회를 시작합니다.")
        else:
            self.logger.warning("TimeManager가 설정되지 않아 시장 개장 여부를 확인할 수 없습니다. 조회를 시도합니다.")

        if self._env.is_paper_trading:
            self.logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}

        top_stocks_response = self.get_top_market_cap_stocks("0000")

        if not top_stocks_response or top_stocks_response.get('rt_cd') != '0':
            self.logger.error(f"시가총액 상위 종목 조회 실패: {top_stocks_response}")
            return None

        top_stocks_list = top_stocks_response.get('output', [])
        if not top_stocks_list:
            self.logger.info("시가총액 상위 종목 목록을 찾을 수 없습니다.")
            return None

        results = []
        for i, stock_info in enumerate(top_stocks_list):
            if i >= 10:
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

        if results:
            self.logger.info("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")
            return results
        else:
            self.logger.warning("시가총액 1~10위 종목 현재가 조회 결과 없음.")
            return None
