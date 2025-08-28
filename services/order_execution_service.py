# app/order_execution_service.py
import asyncio
from common.types import ErrorCode, ResCommonResponse


class OrderExecutionService:
    """
    주식 매수/매도 주문 및 실시간 체결가/호가 구독 관련 핸들러를 관리하는 클래스입니다.
    TradingService, Logger, TimeManager 인스턴스를 주입받아 사용합니다.
    """

    def __init__(self, trading_service, logger, time_manager):
        self.trading_service = trading_service
        self.logger = logger
        self.time_manager = time_manager

    async def handle_place_buy_order(self, stock_code, price, qty):
        """주식 매수 주문 요청 및 결과 출력."""
        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
            return None  # 주문 실패 시 None 반환하도록 수정 (또는 실패 응답 딕셔너리)

        buy_order_result: ResCommonResponse = await self.trading_service.place_buy_order(
            stock_code, price, qty
        )
        if buy_order_result and buy_order_result.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(
                f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{buy_order_result.rt_cd}', 'msg1': '{buy_order_result.msg1}'}}")
        else:
            self.logger.error(
                f"주식 매수 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{buy_order_result.rt_cd}', 'msg1': '{buy_order_result.msg1}'}}")
        return buy_order_result

    async def handle_place_sell_order(self, stock_code, price, qty):
        """주식 매도 주문 요청 및 결과 출력."""
        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
            return None  # 주문 실패 시 None 반환

        sell_order_result: ResCommonResponse = await self.trading_service.place_sell_order(
            stock_code, price, qty
        )
        if sell_order_result and sell_order_result.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(
                f"주식 매도 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{sell_order_result.rt_cd}', 'msg1': '{sell_order_result.msg1}'}}")
        else:
            self.logger.error(
                f"주식 매도 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{sell_order_result.rt_cd}', 'msg1': '{sell_order_result.msg1}'}}")
        return sell_order_result

    async def handle_buy_stock(self, stock_code, qty_input, price_input):  # 파라미터 추가
        """
        사용자 입력을 받아 주식 매수 주문을 처리합니다.
        trading_app.py의 '3'번 옵션에 매핑됩니다.
        """

        try:
            qty = int(qty_input)
            price = int(price_input)
        except ValueError:
            msg = f"잘못된 매수 입력: 수량={qty_input}, 가격={price_input}"
            self.logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)

        # handle_place_buy_order 호출
        return await self.handle_place_buy_order(stock_code, price, qty)

    async def handle_sell_stock(self, stock_code, qty_input, price_input):  # 파라미터 추가
        """
        사용자 입력을 받아 주식 매도 주문을 처리합니다.
        trading_app.py의 '4'번 옵션에 매핑됩니다.
        """
        try:
            qty = int(qty_input)
            price = int(price_input)
        except ValueError:
            msg = f"잘못된 매도 입력: 수량={qty_input}, 가격={price_input}"
            self.logger.warning(msg)
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)

        # handle_place_sell_order 호출
        return await self.handle_place_sell_order(stock_code, price, qty)

    async def handle_realtime_price_quote_stream(self, stock_code):
        """
        실시간 주식 체결가/호가 스트림을 시작하고,
        사용자 입력이 있을 때까지 데이터를 수신합니다.
        """
        print(f"\n--- 실시간 주식 체결가/호가 구독 시작 ({stock_code}) ---")
        print("실시간 데이터를 수신 중입니다... (종료하려면 Enter를 누르세요)")

        # 콜백 함수 정의
        def realtime_data_display_callback(data):
            if isinstance(data, dict):
                data_type = data.get('type')
                output = data.get('data', {})

                if data_type == 'realtime_price':  # 주식 체결
                    current_price = output.get('STCK_PRPR', 'N/A')
                    acml_vol = output.get('ACML_VOL', 'N/A')
                    trade_time = output.get('STCK_CNTG_HOUR', 'N/A')
                    change_val = output.get('PRDY_VRSS', 'N/A')
                    change_sign = output.get('PRDY_VRSS_SIGN', 'N/A')
                    change_rate = output.get('PRDY_CTRT', 'N/A')

                    display_message = (
                        f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                        f"전일대비: {change_sign}{change_val} ({change_rate}%), 누적량: {acml_vol}"
                    )
                    print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'realtime_quote':  # 주식 호가
                    askp1 = output.get('매도호가1', 'N/A')
                    bidp1 = output.get('매수호가1', 'N/A')
                    trade_time = output.get('영업시간', 'N/A')
                    display_message = (
                        f"\r[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1: {askp1}, 매수1: {bidp1}{' ' * 20}"
                    )
                    print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'signing_notice':  # 체결 통보
                    order_num = output.get('주문번호', 'N/A')
                    trade_qty = output.get('체결수량', 'N/A')
                    trade_price = output.get('체결단가', 'N/A')
                    trade_time = output.get('주식체결시간', 'N/A')
                    print(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")
                else:
                    self.logger.debug(f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}")

        # 웹소켓 연결 및 구독 요청
        if await self.trading_service.connect_websocket(on_message_callback=realtime_data_display_callback):
            await self.trading_service.subscribe_realtime_price(stock_code)
            await self.trading_service.subscribe_realtime_quote(stock_code)

            try:
                await asyncio.to_thread(input)
                print("\n")

            except KeyboardInterrupt:
                print("\n사용자에 의해 실시간 구독이 중단됩니다.")
                self.logger.info("실시간 구독 중단 (KeyboardInterrupt).")
            finally:
                await self.trading_service.unsubscribe_realtime_price(stock_code)
                await self.trading_service.unsubscribe_realtime_quote(stock_code)
                await self.trading_service.disconnect_websocket()
                print("실시간 주식 스트림을 종료했습니다.")
                self.logger.info(f"실시간 주식 스트림 종료: 종목={stock_code}")
        else:
            print("실시간 웹소켓 연결에 실패했습니다.")
            self.logger.error("실시간 웹소켓 연결 실패.")
