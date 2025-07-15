import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService
from common.types import ResCommonResponse, ErrorCode, ResTopMarketCapApiItem

import unittest

class TestTradingServiceTopStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()
        self.mock_time_manager = MagicMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_get_top_10_market_cap_stocks_when_missing_stock_code(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data=[
                ResTopMarketCapApiItem(
                    iscd="000001",
                    mksc_shrn_iscd="",
                    stck_avls="1000000000",
                    data_rank="1",
                    hts_kor_isnm="종목1",
                    acc_trdvol="50000"
                ),
                ResTopMarketCapApiItem(
                    iscd="000660",
                    mksc_shrn_iscd="000660",
                    stck_avls="2000000000",
                    data_rank="2",
                    hts_kor_isnm="종목2",
                    acc_trdvol="100000"
                )
            ]
        ))

        # ✅ 실제로 사용되는 트레이딩 서비스의 메서드를 Mock 처리해야 함
        self.trading_service.get_current_stock_price = AsyncMock(return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data={"stck_prpr": "10000"}
        ))

        result : ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # 이제는 리스트 1개가 반환되어야 함
        self.assertEqual(len(result.data), 1)

        # 로그 메시지 확인
        self.assertTrue(
            any("종목코드를 찾을 수 없습니다" in call.args[0]
                for call in self.mock_logger.warning.call_args_list)
        )


