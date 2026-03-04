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
            return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)

        buy_order_result: ResCommonResponse = await self.trading_service.place_buy_order(
            stock_code, price, qty
        )
        if buy_order_result and buy_order_result.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(
                f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{buy_order_result.rt_cd}', 'msg1': '{buy_order_result.msg1}'}}")
        else:
            rt_cd = buy_order_result.rt_cd if buy_order_result else 'None'
            msg1 = buy_order_result.msg1 if buy_order_result else '응답 없음'
            self.logger.error(
                f"주식 매수 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{rt_cd}', 'msg1': '{msg1}'}}")
        return buy_order_result

    async def handle_place_sell_order(self, stock_code, price, qty):
        """주식 매도 주문 요청 및 결과 출력."""
        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.MARKET_CLOSED.value, msg1="장 마감 시간에는 주문할 수 없습니다.", data=None)

        sell_order_result: ResCommonResponse = await self.trading_service.place_sell_order(
            stock_code, price, qty
        )
        if sell_order_result and sell_order_result.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(
                f"주식 매도 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '{sell_order_result.rt_cd}', 'msg1': '{sell_order_result.msg1}'}}")
        else:
            rt_cd = sell_order_result.rt_cd if sell_order_result else 'None'
            msg1 = sell_order_result.msg1 if sell_order_result else '응답 없음'
            self.logger.error(
                f"주식 매도 주문 실패: 종목={stock_code}, 결과={{'rt_cd': '{rt_cd}', 'msg1': '{msg1}'}}")
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

    async def sell_all_stocks(self):
        """보유하고 있는 모든 주식을 시장가로 매도합니다."""
        self.logger.info("모든 보유 주식의 일괄 매도를 시작합니다.")
        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 일괄 매도를 진행할 수 없습니다.")
            return {"error": "장 마감 시간에는 일괄 매도를 할 수 없습니다."}

        try:
            # 1. 보유 주식 목록 조회
            balance_response = await self.trading_service.get_account_balance()
            if balance_response.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"잔고 조회 실패: {balance_response.msg1}")
                return {"error": f"잔고 조회에 실패했습니다: {balance_response.msg1}"}

            holdings = balance_response.data.get('output1', [])
            if not holdings:
                self.logger.info("매도할 보유 주식이 없습니다.")
                return {"message": "보유 중인 주식이 없습니다.", "results": []}

            # 2. 각 주식에 대해 매도 주문 실행
            sell_tasks = []
            for stock in holdings:
                stock_code = stock.get('pdno')
                quantity = int(stock.get('hldg_qty', 0))
                
                if stock_code and quantity > 0:
                    # 시장가 주문을 위해 가격을 0으로 설정
                    task = self.trading_service.place_sell_order(stock_code, 0, quantity, order_type="시장가")
                    sell_tasks.append((stock_code, task))

            if not sell_tasks:
                self.logger.info("매도할 유효한 주식이 없습니다.")
                return {"message": "매도할 유효한 주식이 없습니다.", "results": []}

            # 3. 매도 주문 결과 집계
            results = []
            for stock_code, task in sell_tasks:
                try:
                    result = await task
                    if result and result.rt_cd == ErrorCode.SUCCESS.value:
                        self.logger.info(f"매도 주문 성공: {stock_code}")
                        results.append({"stock_code": stock_code, "success": True, "message": result.msg1})
                    else:
                        msg = result.msg1 if result else "알 수 없는 오류"
                        self.logger.error(f"매도 주문 실패: {stock_code}, 이유: {msg}")
                        results.append({"stock_code": stock_code, "success": False, "message": msg})
                except Exception as e:
                    self.logger.error(f"매도 주문 중 예외 발생: {stock_code}, 오류: {str(e)}")
                    results.append({"stock_code": stock_code, "success": False, "message": str(e)})

            self.logger.info("일괄 매도 절차가 완료되었습니다.")
            return {"message": "일괄 매도가 완료되었습니다.", "results": results}

        except Exception as e:
            self.logger.critical(f"일괄 매도 중 심각한 오류 발생: {e}", exc_info=True)
            return {"error": f"일괄 매도 중 심각한 오류가 발생했습니다: {str(e)}"}

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
