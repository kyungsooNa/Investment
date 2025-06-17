# services/trading_service.py (부분)
from api.client import KoreaInvestAPI
from api.env import KoreaInvestEnv
from core.time_manager import TimeManager
import logging
import datetime
import time  # sleep_duration을 위해 time.sleep 사용
import json  # _default_realtime_message_handler 에서 필요


class TradingService:
    # ... (생략) ...
    def __init__(self, api_client: KoreaInvestAPI, env: KoreaInvestEnv, logger=None, time_manager: TimeManager = None):
        self._api_client = api_client
        self._env = env
        self.logger = logger if logger else logging.getLogger(__name__)
        self._time_manager = time_manager

        self._realtime_data_handler_callback = None  # 외부 콜백을 여기에 저장

    # --- 실시간 웹소켓 메시지를 처리할 기본 핸들러 ---
    def _default_realtime_message_handler(self, data):
        """
        웹소켓으로부터 수신된 실시간 데이터를 처리하는 기본 핸들러.
        여기서 데이터를 파싱하여 콘솔에 보기 좋게 출력합니다.
        """
        # data는 api/websocket_api.py에서 파싱된 딕셔너리라고 가정
        self.logger.info(f"실시간 데이터 수신: Type={data.get('type')}, TR_ID={data.get('tr_id')}, Data={data.get('data')}")

        # 주식 체결 데이터 (H0STCNT0) 처리
        if data.get('type') == 'realtime_price':
            realtime_data = data.get('data', {})
            stock_code = realtime_data.get('유가증권단축종목코드', 'N/A')
            current_price = realtime_data.get('주식현재가', 'N/A')
            change = realtime_data.get('전일대비', 'N/A')
            change_sign = realtime_data.get('전일대비부호', 'N/A')
            cumulative_volume = realtime_data.get('누적거래량', 'N/A')
            trade_time = realtime_data.get('주식체결시간', 'N/A')

            display_message = f"[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, 전일대비: {change_sign}{change}, 누적거래량: {cumulative_volume}"
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")  # 콘솔 덮어쓰기
            self.logger.info(f"실시간 체결 데이터: {stock_code} 현재가={current_price}, 누적량={cumulative_volume}")

        # 주식 호가 데이터 (H0STASP0) 처리
        elif data.get('type') == 'realtime_quote':
            quote_data = data.get('data', {})
            stock_code = quote_data.get('유가증권단축종목코드', 'N/A')
            askp1 = quote_data.get('매도호가1', 'N/A')
            bidp1 = quote_data.get('매수호가1', 'N/A')
            trade_time = quote_data.get('영업시간', 'N/A')  # 호가 시간

            display_message = f"[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1호가: {askp1}, 매수1호가: {bidp1}"
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")  # 콘솔 덮어쓰기
            self.logger.info(f"실시간 호가 데이터: {stock_code} 매도1={askp1}, 매수1={bidp1}")

        # 체결 통보 데이터 처리
        elif data.get('type') == 'signing_notice':
            notice_data = data.get('data', {})
            order_num = notice_data.get('주문번호', 'N/A')
            trade_qty = notice_data.get('체결수량', 'N/A')
            trade_price = notice_data.get('체결단가', 'N/A')
            trade_time = notice_data.get('주식체결시간', 'N/A')

            print(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")  # 새로운 줄에 출력
            self.logger.info(f"체결통보: 주문={order_num}, 수량={trade_qty}, 단가={trade_price}")
        else:
            self.logger.debug(f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}")

    # --- 웹소켓 연결 및 해지 (async) ---
    async def connect_websocket(self, on_message_callback=None):
        """웹소켓 연결을 비동기로 시작합니다."""
        return await self._api_client.websocket.connect(
            on_message_callback=on_message_callback if on_message_callback else self._default_realtime_message_handler)

    async def disconnect_websocket(self):
        """웹소켓 연결을 비동기로 종료합니다."""
        return await self._api_client.websocket.disconnect()

    # --- 실시간 구독/해지 (async) ---
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
    # ... (기존 다른 메서드들은 생략) ...