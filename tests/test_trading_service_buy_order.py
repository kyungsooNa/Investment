import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService


class TestTradingServiceBuyOrder(IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_time_manager = MagicMock()
        self.mock_env = MagicMock()
        self.mock_broker_api_wrapper = AsyncMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_place_buy_order_success(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = {
            "rt_cd": "0", "msg1": "주문 성공"
        }

        result = await self.trading_service.place_buy_order(
            stock_code="005930",
            price="70000",
            qty="10",
            order_dvsn="00"
        )

        self.mock_logger.info.assert_any_call(
            "Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 10, 가격: 70000"
        )

        self.mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(
            stock_code="005930",
            order_price="70000",
            order_qty="10",
            trade_type="buy",
            order_dvsn="00"
        )

        assert result == {"rt_cd": "0", "msg1": "주문 성공"}


    async def test_place_buy_order_failure(self):
        self.mock_broker_api_wrapper.place_stock_order.side_effect = Exception("API 오류 발생")

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("API 오류 발생", str(context.exception))

        # 로그 메시지 일부만 포함되었는지 확인
        self.assertTrue(
            any("매수 주문 중 오류 발생" in call_args[0][0] for call_args in self.mock_logger.error.call_args_list)
        )

    async def test_place_buy_order_api_response_failure(self):
        # API 호출은 성공하지만, 실패 응답 반환 (예: 주문가능금액 부족)
        self.mock_broker_api_wrapper.place_stock_order.return_value = {
            "rt_cd": "1",
            "msg1": "주문가능금액 부족"
        }

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("주문가능금액 부족", str(context.exception))

        # 실패 로그 확인 (일부 문자열 포함 여부로 검증)
        self.assertTrue(
            any("매수 주문 실패" in call_args[0][0] for call_args in self.mock_logger.error.call_args_list)
        )

    async def test_place_buy_order_response_missing_rt_cd(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = {}

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("매수 주문 실패", str(context.exception))
        self.assertTrue(
            any("매수 주문 실패" in call_args[0][0] for call_args in self.mock_logger.error.call_args_list)
        )

    async def test_place_buy_order_logs_info_called_even_on_failure(self):
        self.mock_broker_api_wrapper.place_stock_order.side_effect = Exception("API 에러")

        with self.assertRaises(Exception):
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.mock_logger.info.assert_any_call(
            "Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 10, 가격: 70000"
        )

    async def test_place_buy_order_response_missing_msg1(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = {"rt_cd": "1"}  # msg1 없음

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("매수 주문 실패", str(context.exception))  # 기본 메시지 확인
        self.mock_logger.error.assert_any_call("매수 주문 실패: 매수 주문 실패")

    async def test_place_buy_order_called_with_expected_arguments(self):
        self.mock_broker_api_wrapper.place_stock_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "주문 성공"}
        )

        await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(
            stock_code="005930",
            order_price="70000",
            order_qty="10",
            trade_type="buy",
            order_dvsn="00"
        )

    async def test_place_buy_order_api_failure(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = {
            "rt_cd": "1", "msg1": "매수 불가"
        }

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("매수 불가", str(context.exception))
        self.mock_logger.error.assert_any_call("매수 주문 실패: 매수 불가")

    async def test_place_buy_order_exception_logging(self):
        self.mock_broker_api_wrapper.place_stock_order.side_effect = Exception("예상치 못한 오류")

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", "70000", "10", "00")

        self.assertIn("예상치 못한 오류", str(context.exception))
        self.mock_logger.error.assert_any_call("Service - 매수 주문 중 오류 발생: 예상치 못한 오류")
