import pytest
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResMarketCapStockItem,ResStockFullInfoApiOutput
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
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "10000",
                    "prdy_ctrt": "2.35",
                    "prdy_vrss_sign": "1"
                })
            }
        ))

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # 이제는 리스트 1개가 반환되어야 함
        assert isinstance(result.data, List)
        assert len(result.data) == 1

        # 로그 메시지 확인
        self.assertTrue(
            any("종목코드를 찾을 수 없습니다" in call.args[0]
                for call in self.mock_logger.warning.call_args_list)
        )

    @pytest.mark.asyncio
    async def test_market_closed_returns_none(self):
        self.mock_time_manager.is_market_open.return_value = False

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # 수정된 기대값 검증
        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.INVALID_INPUT.value
        assert result.msg1 == "시장이 닫혀 있어 조회 불가"
        assert isinstance(result.data, List)
        assert result.data == []

        self.mock_logger.warning.assert_any_call("시장이 닫혀 있어 시가총액 1~10위 종목 현재가 조회를 수행할 수 없습니다.")

    @pytest.mark.asyncio
    async def test_paper_trading_returns_error(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

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

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert "API 오류" in result.msg1
        assert isinstance(result.data, List)
        self.mock_logger.error.assert_called()

    async def test_get_top_stocks_empty_list(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="조회 성공", data=[])
        )

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert isinstance(result.data, List)
        assert result.data == []
        self.mock_logger.info.assert_any_call("시가총액 상위 종목 목록을 찾을 수 없습니다.")

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
                hts_kor_isnm="삼성전자",
                acc_trdvol="1000000"
            ),
            ResTopMarketCapApiItem(
                iscd="000660",
                mksc_shrn_iscd="000660",
                stck_avls="500000000000",
                data_rank="2",
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

        self.trading_service.get_current_stock_price = AsyncMock(
            side_effect=[
                ResCommonResponse(
                    rt_cd="0",
                    msg1="성공",
                    data={"output": ResStockFullInfoApiOutput.from_dict({"stck_prpr": "80000"})}
                ),
                ResCommonResponse(
                    rt_cd="0",
                    msg1="성공",
                    data={"output": ResStockFullInfoApiOutput.from_dict({"stck_prpr": "130000"})}
                ),
            ]
        )

        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, List)
        assert len(result.data) == 2

        assert result.data[0] == ResMarketCapStockItem(
            rank="1", name="삼성전자", code="005930", current_price="80000"
        )
        assert result.data[1] == ResMarketCapStockItem(
            rank="2", name="SK하이닉스", code="000660", current_price="130000"
        )

    async def test_partial_price_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        top_stocks = [
            ResTopMarketCapApiItem(
                iscd="005930", mksc_shrn_iscd="005930", stck_avls="...", data_rank="1", hts_kor_isnm="삼성전자",
                acc_trdvol="..."
            ),
            ResTopMarketCapApiItem(
                iscd="000660", mksc_shrn_iscd="000660", stck_avls="...", data_rank="2", hts_kor_isnm="SK하이닉스",
                acc_trdvol="..."
            ),
        ]

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="조회 성공", data=top_stocks)
        )

        self.trading_service.get_current_stock_price = AsyncMock(side_effect=[
            # 첫 번째 종목 실패
            ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="실패", data=None),

            # 두 번째 종목 성공 (✅ 'output'으로 감싸기)
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data={"output": ResStockFullInfoApiOutput.from_dict({"stck_prpr": "130000"})}
            ),
        ])
        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, List)
        assert result.data == [
            ResMarketCapStockItem(rank="2", name="SK하이닉스", code="000660", current_price="130000")
        ]

    @pytest.mark.asyncio
    async def test_market_cap_limit_10_enforced(self):
        # ─ Conditions ─
        self.trading_service._time_manager.is_market_open.return_value = True
        self.trading_service._env.is_paper_trading = False

        # 11개 종목 제공
        top_stocks = [
            ResTopMarketCapApiItem(
                iscd=f"00000{i}",
                mksc_shrn_iscd=f"00000{i}",
                stck_avls="1000000000",
                data_rank=str(i + 1),
                hts_kor_isnm=f"종목{i}",
                acc_trdvol="100000"
            )
            for i in range(11)
        ]

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=top_stocks
            )
        )

        # get_current_stock_price 모킹 (11개 응답 준비)
        self.trading_service.get_current_stock_price = AsyncMock(
            side_effect=[
                ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="성공",
                    data={"output": ResStockFullInfoApiOutput.from_dict({"stck_prpr": str(10000 + i)})}
                )
                for i in range(11)
            ]
        )

        # 실행
        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # 검증
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, List)
        assert len(result.data) == 10
        assert all(isinstance(item, ResMarketCapStockItem) for item in result.data)
        self.trading_service.get_current_stock_price.assert_awaited()
        assert self.trading_service.get_current_stock_price.await_count == 10

    @pytest.mark.asyncio
    async def test_top10_result_none_logs_warning(self):
        self.trading_service._time_manager.is_market_open.return_value = True
        self.trading_service._env.is_paper_trading = False

        # 정상 종목 1개지만 현재가 조회 실패
        mock_top_stocks = [
            ResTopMarketCapApiItem(
                iscd="005930",
                mksc_shrn_iscd="005930",
                stck_avls="...",
                data_rank="1",
                hts_kor_isnm="삼성전자",
                acc_trdvol="..."
            )
        ]

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=mock_top_stocks
            )
        )

        self.trading_service.get_current_stock_price = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="조회 실패",
                data=None
            )
        )

        # Act
        result: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # Assert
        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert isinstance(result.data, List)
        assert result.data == []
        self.trading_service._logger.warning.assert_any_call("시가총액 1~10위 종목 현재가 조회 결과 없음.")
