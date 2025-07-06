import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from io import StringIO
import builtins # print 함수 목킹을 위해 필요
from unittest.mock import call, ANY

# 테스트 대상 모듈 임포트
from app.transaction_handlers import TransactionHandlers

# 테스트를 위한 MockLogger
class MockLogger:
    def __init__(self):
        self.info = MagicMock()
        self.debug = MagicMock()
        self.warning = MagicMock()
        self.error = MagicMock()
        self.critical = MagicMock()

class TestTransactionHandlers(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.mock_trading_service = AsyncMock()
        self.mock_logger = MockLogger()
        self.mock_time_manager = MagicMock()

        # time_manager의 is_market_open 기본값 설정
        self.mock_time_manager.is_market_open.return_value = True

        # trading_service.get_user_input은 문자열을 반환해야 함
        self.mock_trading_service.get_user_input = AsyncMock(side_effect=["005930", "10", "70000"])

        # print 함수 출력을 캡처 (콘솔 출력 검증용)
        self.original_print = builtins.print
        self.print_output_capture = StringIO()
        builtins.print = lambda *args, **kwargs: self.print_output_capture.write(' '.join(map(str, args)) + '\n')

        self.handler = TransactionHandlers(
            trading_service=self.mock_trading_service,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

    def tearDown(self):
        builtins.print = self.original_print
        self.print_output_capture.close()

    # --- handle_buy_stock 테스트 ---
    async def test_handle_buy_stock_success(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
        # handle_place_buy_order가 성공적으로 실행되도록 목 설정 (실제 TradingService.place_buy_order 아님)
        self.handler.handle_place_buy_order = AsyncMock(return_value={"rt_cd": "0"})

        await self.handler.handle_buy_stock()

        self.mock_trading_service.get_user_input.assert_has_awaits([
            unittest.mock.call("매수할 종목 코드를 입력하세요: "),
            unittest.mock.call("매수할 수량을 입력하세요: "),
            unittest.mock.call("매수 가격을 입력하세요 (시장가: 0): ")
        ])
        self.handler.handle_place_buy_order.assert_awaited_once_with("005930", 70000, 10, "01")
        self.assertIn("--- 주식 매수 주문 ---", self.print_output_capture.getvalue())

    async def test_handle_buy_stock_market_closed(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
        self.mock_time_manager.is_market_open.return_value = False # 시장 마감

        await self.handler.handle_buy_stock()

        self.mock_trading_service.get_user_input.assert_has_awaits([
            unittest.mock.call("매수할 종목 코드를 입력하세요: "),
            unittest.mock.call("매수할 수량을 입력하세요: "),
            unittest.mock.call("매수 가격을 입력하세요 (시장가: 0): ")
        ])
        self.assertIn("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.\n", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_with("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")


    async def test_handle_buy_stock_invalid_input(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "abc", "70000"] # 잘못된 수량 입력
        self.handler.handle_place_buy_order = AsyncMock() # 호출되지 않아야 함

        await self.handler.handle_buy_stock()

        self.mock_trading_service.get_user_input.assert_has_awaits([
            unittest.mock.call("매수할 종목 코드를 입력하세요: "),
            unittest.mock.call("매수할 수량을 입력하세요: "),
            unittest.mock.call("매수 가격을 입력하세요 (시장가: 0): ")
        ])
        self.assertIn("잘못된 수량 또는 가격 입력입니다.\n", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_once()
        self.handler.handle_place_buy_order.assert_not_awaited()

    async def test_handle_buy_stock_place_order_delegation_failure(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "10", "70000"]
        # handle_place_buy_order가 실패 응답을 반환하도록 목 설정
        # -> handle_place_buy_order가 호출하는 trading_service.place_buy_order를 목킹
        self.mock_trading_service.place_buy_order.return_value = {"rt_cd": "1", "msg1": "주문 실패"}

        await self.handler.handle_buy_stock()

        # handle_place_buy_order 내부의 trading_service.place_buy_order 호출을 검증
        self.mock_trading_service.place_buy_order.assert_awaited_once_with("005930", 70000, 10, "01")
        self.assertIn("주식 매수 주문 실패: {'rt_cd': '1', 'msg1': '주문 실패'}", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()

    # --- handle_sell_stock 테스트 ---
    async def test_handle_sell_stock_success(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
        self.handler.handle_place_sell_order = AsyncMock(return_value={"rt_cd": "0"})

        await self.handler.handle_sell_stock()

        self.mock_trading_service.get_user_input.assert_has_awaits([
            unittest.mock.call("매도할 종목 코드를 입력하세요: "),
            unittest.mock.call("매도할 수량을 입력하세요: "),
            unittest.mock.call("매도 가격을 입력하세요 (시장가: 0): ")
        ])
        self.handler.handle_place_sell_order.assert_awaited_once_with("005930", 60000, 5, "01")
        self.assertIn("--- 주식 매도 주문 ---", self.print_output_capture.getvalue())

    async def test_handle_sell_stock_market_closed(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
        self.mock_time_manager.is_market_open.return_value = False

        await self.handler.handle_sell_stock()

        self.assertIn("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.\n", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_with("시장이 닫혀 있어 매도 주문을 제출하지 못했습니다.")

    async def test_handle_sell_stock_invalid_input(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "xyz", "60000"]
        self.handler.handle_place_sell_order = AsyncMock()

        await self.handler.handle_sell_stock()

        self.assertIn("잘못된 수량 또는 가격 입력입니다.\n", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_once()
        self.handler.handle_place_sell_order.assert_not_awaited()

    async def test_handle_sell_stock_place_order_delegation_failure(self):
        self.mock_trading_service.get_user_input.side_effect = ["005930", "5", "60000"]
        self.mock_trading_service.place_sell_order.return_value = {"rt_cd": "1", "msg1": "매도 실패"} # 수정: place_sell_order를 목킹

        await self.handler.handle_sell_stock()

        self.mock_trading_service.place_sell_order.assert_awaited_once_with("005930", 60000, 5, "01") # 수정: assertion 대상 변경
        self.mock_logger.error.assert_called_once_with(f"주식 매도 주문 실패: 종목=005930, 결과={{'rt_cd': '1', 'msg1': '매도 실패'}}") # 수정: print 대신 logger 검증

    # --- handle_place_buy_order 테스트 ---
    async def test_handle_place_buy_order_success(self):
        self.mock_trading_service.place_buy_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}

        result = await self.handler.handle_place_buy_order("005930", 70000, 10, "01")

        # 여기를 위치 인자로 수정합니다.
        self.mock_trading_service.place_buy_order.assert_awaited_once_with(
            "005930", 70000, 10, "01"
        )
        self.assertIn("--- 주식 매수 주문 시도 ---", self.print_output_capture.getvalue())
        self.assertIn("주식 매수 주문 성공", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once()
        self.assertEqual(result, {"rt_cd": "0", "msg1": "주문 성공"})

    async def test_handle_place_buy_order_trading_service_failure(self):
        self.mock_trading_service.place_buy_order.return_value = {"rt_cd": "1", "msg1": "잔고 부족"}

        result = await self.handler.handle_place_buy_order("005930", 70000, 10, "01")

        self.assertIn("주식 매수 주문 실패: {'rt_cd': '1', 'msg1': '잔고 부족'}", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()
        self.assertEqual(result, {"rt_cd": "1", "msg1": "잔고 부족"})

    # --- handle_place_sell_order 테스트 ---
    async def test_handle_place_sell_order_success(self):
        self.mock_trading_service.place_sell_order.return_value = {"rt_cd": "0", "msg1": "매도 성공"}

        result = await self.handler.handle_place_sell_order("005930", 60000, 5, "01")

        # 여기를 위치 인자로 수정합니다.
        self.mock_trading_service.place_sell_order.assert_awaited_once_with(
            "005930", 60000, 5, "01"
        )
        self.assertIn("--- 주식 매도 주문 시도 ---", self.print_output_capture.getvalue())
        self.assertIn("주식 매도 주문 성공", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once()
        self.assertEqual(result, {"rt_cd": "0", "msg1": "매도 성공"})

    async def test_handle_place_sell_order_trading_service_failure(self):
        self.mock_trading_service.place_sell_order.return_value = {"rt_cd": "1", "msg1": "수량 부족"}

        result = await self.handler.handle_place_sell_order("005930", 60000, 5, "01")

        self.assertIn("주식 매도 주문 실패: {'rt_cd': '1', 'msg1': '수량 부족'}", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()
        self.assertEqual(result, {"rt_cd": "1", "msg1": "수량 부족"})

    # --- handle_realtime_price_quote_stream 테스트 ---
    async def test_handle_realtime_price_quote_stream_success(self):
        self.mock_trading_service.connect_websocket.return_value = True
        self.mock_trading_service.subscribe_realtime_price.return_value = True
        self.mock_trading_service.subscribe_realtime_quote.return_value = True
        self.mock_trading_service.unsubscribe_realtime_price.return_value = True
        self.mock_trading_service.unsubscribe_realtime_quote.return_value = True
        self.mock_trading_service.disconnect_websocket.return_value = True

        with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            await self.handler.handle_realtime_price_quote_stream("005930")

            self.mock_trading_service.connect_websocket.assert_awaited_once_with(
                on_message_callback=ANY # ANY로 변경
            )
            self.mock_trading_service.subscribe_realtime_price.assert_awaited_once_with("005930")
            self.mock_trading_service.subscribe_realtime_quote.assert_awaited_once_with("005930")
            self.mock_trading_service.unsubscribe_realtime_price.assert_awaited_once_with("005930")
            self.mock_trading_service.unsubscribe_realtime_quote.assert_awaited_once_with("005930")
            self.mock_trading_service.disconnect_websocket.assert_awaited_once()
            self.assertIn("--- 실시간 주식 체결가/호가 구독 시작 (005930) ---", self.print_output_capture.getvalue())
            self.assertIn("실시간 주식 스트림을 종료했습니다.", self.print_output_capture.getvalue())
            self.mock_logger.info.assert_called_once_with(f"실시간 주식 스트림 종료: 종목=005930")

    async def test_handle_realtime_price_quote_stream_connection_failure(self):
        self.mock_trading_service.connect_websocket.return_value = False

        await self.handler.handle_realtime_price_quote_stream("005930")

        self.mock_trading_service.connect_websocket.assert_awaited_once()
        self.mock_trading_service.subscribe_realtime_price.assert_not_awaited() # 연결 실패 시 구독 안함
        self.assertIn("실시간 웹소켓 연결에 실패했습니다.\n", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once_with("실시간 웹소켓 연결 실패.")

    async def test_handle_realtime_price_quote_stream_keyboard_interrupt(self):
        self.mock_trading_service.connect_websocket.return_value = True
        self.mock_trading_service.subscribe_realtime_price.return_value = True
        self.mock_trading_service.subscribe_realtime_quote.return_value = True
        self.mock_trading_service.unsubscribe_realtime_price.return_value = True
        self.mock_trading_service.unsubscribe_realtime_quote.return_value = True
        self.mock_trading_service.disconnect_websocket.return_value = True

        with patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = KeyboardInterrupt

            await self.handler.handle_realtime_price_quote_stream("005930")

            self.assertIn("사용자에 의해 실시간 구독이 중단됩니다.", self.print_output_capture.getvalue())
            # logger.info의 모든 호출을 검증
            self.mock_logger.info.assert_has_calls([
                call("실시간 구독 중단 (KeyboardInterrupt)."),
                call(f"실시간 주식 스트림 종료: 종목=005930")
            ])
            self.mock_trading_service.unsubscribe_realtime_price.assert_awaited_once()
            self.mock_trading_service.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_buy_order_when_market_closed():
    mock_logger = MagicMock()
    mock_time_manager = MagicMock()
    mock_time_manager.is_market_open.return_value = False

    mock_trading_service = MagicMock()

    handlers = TransactionHandlers(
        trading_service=mock_trading_service,  # ✅ 정확한 인자
        logger=mock_logger,
        time_manager=mock_time_manager
    )

    await handlers.handle_place_buy_order("005930", "70000", "10", "00")

    mock_logger.warning.assert_any_call("시장이 닫혀 있어 매수 주문을 제출하지 못했습니다.")
