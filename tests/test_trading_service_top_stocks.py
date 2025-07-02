import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService

import unittest

class TestTradingServiceTopStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_api_client = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()
        self.mock_time_manager = MagicMock()

        self.trading_service = TradingService(
            api_client=self.mock_api_client,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_get_top_10_market_cap_stocks_when_missing_stock_code(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": [
                {"hts_kor_isnm": "종목1", "data_rank": "1"}  # 종목 코드 누락
            ]
        }
        self.mock_api_client.quotations.get_current_price.return_value = {
            "rt_cd": "0", "output": {"stck_prpr": "10000"}
        }

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNone(result)
        self.mock_logger.warning.assert_any_call(
            "시가총액 상위 종목 목록에서 유효한 종목코드를 찾을 수 없습니다: {'hts_kor_isnm': '종목1', 'data_rank': '1'}"
        )
