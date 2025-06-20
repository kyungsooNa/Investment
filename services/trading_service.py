# services/trading_service.py
from api.client import KoreaInvestAPI
from api.env import KoreaInvestEnv
from core.time_manager import TimeManager
import logging
import datetime
import time  # time.sleep 사용


class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, api_client: KoreaInvestAPI, env: KoreaInvestEnv, logger=None, time_manager: TimeManager = None):
        self._api_client = api_client
        self._env = env
        self.logger = logger if logger else logging.getLogger(__name__)
        self._time_manager = time_manager

        self._realtime_data_handler_callback = None  # 외부 콜백을 여기에 저장

    def _default_realtime_message_handler(self, data):
        """
        웹소켓으로부터 수신된 실시간 데이터를 처리하는 기본 핸들러.
        여기서 데이터를 파싱하여 콘솔에 보기 좋게 출력합니다.
        """
        self.logger.info(f"실시간 데이터 수신: Type={data.get('type')}, TR_ID={data.get('tr_id')}, Data={data.get('data')}")

        # 주식 체결 데이터 (H0STCNT0) 처리
        if data.get('type') == 'realtime_price':
            realtime_data = data.get('data', {})
            stock_code = realtime_data.get('MKSC_SHRN_ISCD', 'N/A')
            current_price = realtime_data.get('STCK_PRPR', 'N/A')
            change = realtime_data.get('PRDY_VRSS', 'N/A')
            change_sign = realtime_data.get('PRDY_VRSS_SIGN', 'N/A')
            change_rate = realtime_data.get('PRDY_CTRT', 'N/A')
            cumulative_volume = realtime_data.get('ACML_VOL', 'N/A')
            trade_time = realtime_data.get('STCK_CNTG_HOUR', 'N/A')

            display_message = (
                f"[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                f"전일대비: {change_sign}{change} ({change_rate}%), 누적량: {cumulative_volume}"
            )
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
            self.logger.info(
                f"실시간 체결 데이터: {stock_code} 현재가={current_price}, 전일대비={change_sign}{change}({change_rate}%), 누적량={cumulative_volume}")

        # 주식 호가 데이터 (H0STASP0) 처리
        elif data.get('type') == 'realtime_quote':
            quote_data = data.get('data', {})
            stock_code = quote_data.get('유가증권단축종목코드', 'N/A')
            askp1 = quote_data.get('매도호가1', 'N/A')
            bidp1 = quote_data.get('매수호가1', 'N/A')
            trade_time = quote_data.get('영업시간', 'N/A')

            display_message = f"[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1호가: {askp1}, 매수1호가: {bidp1}"
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
            self.logger.info(f"실시간 호가 데이터: {stock_code} 매도1={askp1}, 매수1={bidp1}")

        # 체결 통보 데이터 처리
        elif data.get('type') == 'signing_notice':
            notice_data = data.get('data', {})
            order_num = notice_data.get('주문번호', 'N/A')
            trade_qty = notice_data.get('체결수량', 'N/A')
            trade_price = notice_data.get('체결단가', 'N/A')
            trade_time = notice_data.get('주식체결시간', 'N/A')

            print(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")
            self.logger.info(f"체결통보: 주문={order_num}, 수량={trade_qty}, 단가={trade_price}")
        else:
            self.logger.debug(f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}")

    async def connect_websocket(self, on_message_callback=None):
        """웹소켓 연결을 비동기로 시작합니다."""
        return await self._api_client.websocket.connect(
            on_message_callback=on_message_callback if on_message_callback else self._default_realtime_message_handler)

    async def disconnect_websocket(self):
        """웹소켓 연결을 비동기로 종료합니다."""
        return await self._api_client.websocket.disconnect()

    async def subscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가)를 구독합니다."""
        return await self._api_client.websocket.subscribe_realtime_price(stock_code)

    async def unsubscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가) 구독을 해지합니다."""
        return await self._api_client.websocket.unsubscribe_realtime_price(stock_code)

    async def subscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터를 구독합니다."""
        return await self._api_client.websocket.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터 구독을 해지합니다."""
        return await self._api_client.websocket.unsubscribe_realtime_quote(stock_code)

    # --- 주식 현재가 조회 (TradingService에 get_current_stock_price 메서드 정의) ---
    async def get_current_stock_price(self, stock_code):
        """주식 현재가를 조회하고 결과를 반환합니다."""
        self.logger.info(f"Service - {stock_code} 현재가 조회 요청")
        return await self._api_client.quotations.get_current_price(stock_code)

    async def get_account_balance(self):
        """계좌 잔고를 조회하고 결과를 반환합니다 (실전/모의 구분)."""
        self.logger.info(f"Service - 계좌 잔고 조회 요청 (환경: {'모의투자' if self._env.is_paper_trading else '실전'})")
        if self._env.is_paper_trading:
            return await self._api_client.account.get_account_balance()
        else:
            return await self._api_client.account.get_real_account_balance()

    async def place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문을 제출하고 결과를 반환합니다."""
        self.logger.info(f"Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return await self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매수",
            order_dvsn
        )

    async def place_sell_order(self, stock_code, price, qty, order_dvsn):
        """주식 매도 주문을 제출하고 결과를 반환합니다."""
        self.logger.info(f"Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}")
        return await self._api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매도",
            order_dvsn
        )

    async def get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목을 조회하고 결과를 반환합니다 (모의투자 미지원)."""
        self.logger.info(f"Service - 시가총액 상위 종목 조회 요청 - 시장: {market_code}")
        if self._env.is_paper_trading:
            self.logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}

        return await self._api_client.quotations.get_top_market_cap_stocks(market_code)

    async def get_top_10_market_cap_stocks_with_prices(self):
        """
        시가총액 1~10위 종목의 현재가를 조회합니다.
        시장 개장 여부를 확인하고, 모의투자 미지원 API입니다.
        시장 개장까지 대기하며, 한 번 성공하면 결과를 반환하고 프로그램 종료를 유도합니다.
        """
        self.logger.info("Service - 시가총액 1~10위 종목 현재가 조회 요청")

        if self._time_manager:
            while not self._time_manager.is_market_open():
                next_open_time = self._time_manager.get_next_market_open_time()
                time_to_wait_timedelta = next_open_time - self._time_manager.get_current_kst_time()
                wait_seconds_total = int(time_to_wait_timedelta.total_seconds())

                if wait_seconds_total <= 0:
                    self.logger.info("시장 개장 예상 시간 도달. 시장 상태 재확인 중...")
                    if self._time_manager.is_market_open():
                        print("\r" + " " * 80 + "\r", end="")
                        break
                    else:
                        self.logger.warning("예상 개장 시간 이후에도 시장이 열리지 않았습니다. 5초 후 재확인.")
                        self._time_manager.sleep(5)
                        continue

                if wait_seconds_total >= 24 * 3600:
                    self.logger.warning("시장이 닫혀 있으며, 다음 개장 시간이 24시간 초과입니다. 조회를 시도하지 않습니다.")
                    return None

                hours, remainder = divmod(wait_seconds_total, 3600)
                minutes, seconds_remaining_in_minute = divmod(remainder, 60)

                formatted_time_to_wait = ""
                if hours > 0:
                    formatted_time_to_wait += f"{hours}시간 "
                formatted_time_to_wait += f"{minutes}분"

                message_to_display = f"시장 개장까지 대기 중... ({formatted_time_to_wait} 남음)"
                print(f"\r{message_to_display}{' ' * (80 - len(message_to_display))}", end="")
                self.logger.info(f"시장 닫힘: 대기 중... ({formatted_time_to_wait} 남음)")

                sleep_duration = min(wait_seconds_total, 60)
                self._time_manager.sleep(sleep_duration)

            print("\r" + " " * 80 + "\r", end="")
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
                current_price_response = await self.get_current_stock_price(stock_code)  # await 추가
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
