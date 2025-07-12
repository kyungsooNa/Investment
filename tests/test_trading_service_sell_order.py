import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService

import unittest

class TestTradingServiceSellOrder(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()
        self.mock_time_manager = MagicMock()

        self.trading_service = TradingService(
            broker_wrapper=self.mock_broker_wrapper,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_place_sell_order_success(self):
        self.mock_broker_wrapper.place_stock_order.return_value = {
            "rt_cd": "0", "msg1": "주문 성공"
        }

        stock_code = "005930"
        price = "70000"
        qty = "10"
        order_dvsn = "00"

        result = await self.trading_service.place_sell_order(stock_code, price, qty, order_dvsn)

        self.mock_logger.info.assert_any_call(
            f"Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )
        self.mock_broker_wrapper.place_stock_order.assert_called_once_with(
            stock_code=stock_code,
            order_price=price,
            order_qty=qty,
            trade_type="sell", # place_sell_order는 trade_type을 "sell"로 고정합니다.
            order_dvsn=order_dvsn
        )

        assert result == {"rt_cd": "0", "msg1": "주문 성공"}

    async def test_place_sell_order_failure(self):
        self.mock_broker_wrapper.place_stock_order.side_effect = Exception("API 오류 발생")

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_sell_order("005930", "70000", "10", "00")

        self.assertIn("API 오류 발생", str(context.exception))

    async def test_place_sell_order_api_failure(self):
        self.mock_broker_wrapper.place_stock_order.return_value = {
            "rt_cd": "1", "msg1": "매도 불가"
        }

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_sell_order("005930", "70000", "10", "00")

        self.assertIn("매도 불가", str(context.exception))
        self.mock_logger.error.assert_any_call("매도 주문 실패: 매도 불가")

    async def test_place_sell_order_exception_logging(self):
        self.mock_broker_wrapper.place_stock_order.side_effect = Exception("예상치 못한 오류")

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_sell_order("005930", "70000", "10", "00")

        self.assertIn("예상치 못한 오류", str(context.exception))
        self.mock_logger.error.assert_any_call("Service - 매도 주문 중 오류 발생: 예상치 못한 오류")
