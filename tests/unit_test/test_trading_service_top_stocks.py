import pytest
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResStockFullInfoApiOutput
from typing import List
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

    @pytest.mark.asyncio
    async def test_market_closed_returns_none(self):
        self.mock_time_manager.is_market_open.return_value = False

        result: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code="0000",limit=10)

        # 수정된 기대값 검증
        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        assert result.msg1 == "모의투자 미지원 API입니다."
        assert isinstance(result.data, List)
        assert result.data == []

        self.mock_logger.warning.assert_any_call("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")

    @pytest.mark.asyncio
    async def test_paper_trading_returns_error(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code="0000",limit=10)

        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        assert result.msg1 == "모의투자 미지원 API입니다."
        assert isinstance(result.data, List)
        assert result.data == []

    async def test_get_top_stocks_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류", data=[])
        )

        result: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code="0000",limit=10)

        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert "API 오류" in result.msg1
        assert isinstance(result.data, List)

    @pytest.mark.asyncio
    async def test_successful_flow_returns_results(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        top_stocks = [
            ResTopMarketCapApiItem(
                iscd="005930",
                mksc_shrn_iscd="005930",
                stck_avls="1000000000000",
                data_rank="1",
                prdy_vrss_sign="1",
                hts_kor_isnm="삼성전자",
                acc_trdvol="1000000"
            ),
            ResTopMarketCapApiItem(
                iscd="000660",
                mksc_shrn_iscd="000660",
                stck_avls="500000000000",
                data_rank="2",
                prdy_vrss_sign="2",
                hts_kor_isnm="SK하이닉스",
                acc_trdvol="800000"
            ),
        ]

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=top_stocks
            )
        )

        result: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, List)
        assert len(result.data) == 2


    @pytest.mark.asyncio
    async def test_market_cap_limit_10_enforced(self):
        # ─ Conditions ─
        self.trading_service._time_manager.is_market_open.return_value = True
        self.trading_service._env.is_paper_trading = False
        count = 11
        # 11개 종목 제공
        top_stocks = [
            ResTopMarketCapApiItem(
                iscd=f"00000{i}",
                mksc_shrn_iscd=f"00000{i}",
                stck_avls="1000000000",
                data_rank=str(i + 1),
                prdy_vrss_sign=str((i + 1)%5),
                hts_kor_isnm=f"종목{i}",
                acc_trdvol="100000"
            )
            for i in range(count)
        ]

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=top_stocks
            )
        )

        # 실행
        result: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code()

        # 검증
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, List)
        assert len(result.data) == count
        assert all(isinstance(item, ResTopMarketCapApiItem) for item in result.data)

