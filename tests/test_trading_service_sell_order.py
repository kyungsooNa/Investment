import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, AsyncMock

class TestTradingServiceSellOrder(IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_time_manager = MagicMock()
        self.mock_env = MagicMock()
        self.mock_api_client = MagicMock()
        self.mock_api_client.trading = MagicMock()
        self.mock_api_client.trading.place_stock_order = AsyncMock()

        from services.trading_service import TradingService
        self.trading_service = TradingService(
            api_client=self.mock_api_client,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_place_sell_order_success(self):
        self.mock_api_client.trading.place_stock_order.return_value = {
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
        self.mock_api_client.trading.place_stock_order.assert_called_once_with(
            stock_code=stock_code, price=price, qty=qty, bs_type="매도", order_dvsn=order_dvsn
        )
        assert result == {"rt_cd": "0", "msg1": "주문 성공"}

    async def test_place_sell_order_failure(self):
        self.mock_api_client.trading.place_stock_order.side_effect = Exception("API 오류 발생")

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_sell_order("005930", "70000", "10", "00")

        assert "API 오류 발생" in str(context.exception)

    async def test_get_top_10_market_cap_stocks_when_missing_stock_code(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks = AsyncMock(return_value={
            "rt_cd": "0",
            "output": [
                {"hts_kor_isnm": "종목1", "data_rank": "1"}  # 종목 코드 누락 케이스
            ]
        })

        self.mock_api_client.quotations.get_current_price = AsyncMock(return_value={
            "rt_cd": "0", "output": {"stck_prpr": "10000"}
        })

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNone(result)
        self.mock_logger.warning.assert_any_call(
            "시가총액 상위 종목 목록에서 유효한 종목코드를 찾을 수 없습니다: {'hts_kor_isnm': '종목1', 'data_rank': '1'}"
        )
