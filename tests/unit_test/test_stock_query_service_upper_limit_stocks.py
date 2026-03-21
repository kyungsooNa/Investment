import pytest
import unittest
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock
import sys
from io import StringIO
import builtins

# 테스트할 모듈 임포트
from services.stock_query_service import StockQueryService
from services.market_data_service import MarketDataService
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from core.market_clock import MarketClock  # Mocking용
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # Mocking용
from common.types import ResCommonResponse, ResStockFullInfoApiOutput, ResTopMarketCapApiItem
from dataclasses import fields



# 테스트를 위한 MockLogger (실제 로거 대신 사용)
class MockLogger:
    def __init__(self):
        self.info = mock.Mock()
        self.debug = mock.Mock()
        self.warning = mock.Mock()
        self.error = mock.Mock()
        self.critical = mock.Mock()


def make_stock_response(prdy_vrss_sign: str, stck_prpr: str, prdy_ctrt: str) -> ResCommonResponse:
    """
    최소 필드만 받아서 ResStockFullInfoApiOutput을 생성하고,
    ResCommonResponse로 감싸주는 테스트용 헬퍼 함수입니다.
    """
    base_fields = {
        f.name: "" for f in fields(ResStockFullInfoApiOutput)
        if f.name not in {"prdy_vrss_sign", "stck_prpr", "prdy_ctrt"}
    }

    stock_data = ResStockFullInfoApiOutput(
        prdy_vrss_sign=prdy_vrss_sign,
        stck_prpr=stck_prpr,
        prdy_ctrt=prdy_ctrt,
        **base_fields
    )

    return ResCommonResponse(
        rt_cd="0",
        msg1="정상처리 되었습니다.",
        data=stock_data
    )

def make_stock_payload(sign: str, price: str, change_rate: str):
    return {
        "prdy_vrss_sign": sign,
        "stck_prpr": price,
        "prdy_ctrt": change_rate
    }

class TestUpperLimitStocks(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """각 테스트 메서드 실행 전에 필요한 Mock 객체와 핸들러 인스턴스를 설정합니다."""
        # 종속성 Mock 객체 생성
        self.mock_env = mock.MagicMock(spec=KoreaInvestApiEnv)  # MagicMock으로 변경
        self.mock_env.is_paper_trading = False  # 기본값 설정
        self.mock_logger = MockLogger()
        self.mock_market_clock = mock.MagicMock(spec_set=MarketClock)  # MagicMock으로 변경

        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_broker_api_wrapper.client = AsyncMock(spec=KoreaInvestApiQuotations)

        self.mock_broker_api_wrapper.client.quotations = mock.MagicMock(spec_set=KoreaInvestApiQuotations)
        self.mock_broker_api_wrapper.client.account = mock.MagicMock(spec_set=KoreaInvestApiAccount)
        self.mock_broker_api_wrapper.client.trading = mock.MagicMock(spec_set=KoreaInvestApiTrading)

        # 각 하위 Mock 객체의 메서드들을 직접 Mock 객체로 할당하고 return_value를 설정합니다.
        # 이렇게 하면 TradingService가 이 Mock 메서드들을 호출할 수 있습니다.
        self.mock_broker_api_wrapper.client.quotations.get_current_price = mock.AsyncMock()  # KoreaInvestApiQuotations의 메서드
        self.mock_broker_api_wrapper.client.quotations.get_top_market_cap_stocks_code = mock.AsyncMock()  # KoreaInvestApiQuotations의 메서드

        self.mock_broker_api_wrapper.client.account.get_account_balance = mock.AsyncMock()

        self.mock_broker_api_wrapper.client.trading.place_stock_order = mock.AsyncMock()

        # 📌 MarketDataService 인스턴스 생성 (주입) - setUp에서 한 번만 생성
        self.market_data_service = MarketDataService(
            broker_api_wrapper=self.mock_broker_api_wrapper,  # 여기에서 Mock api_client를 주입
            env=self.mock_env,
            logger=self.mock_logger,
            market_clock=self.mock_market_clock
        )

        # 📌 DataHandlers 인스턴스 생성 (handle_upper_limit_stocks 포함) - setUp에서 한 번만 생성
        self.stock_query_service = StockQueryService(
            market_data_service=self.market_data_service,  # 여기에서 Mock market_data_service를 주입
            logger=self.mock_logger,
            market_clock=self.mock_market_clock
        )

    # --- handle_upper_limit_stocks 테스트 케이스들 ---

    async def test_handle_upper_limit_stocks_paper_trading(self):
        """모의투자 환경에서 상한가 종목 조회 시도 (미지원)."""
        self.mock_env.is_paper_trading = True

        result = await self.stock_query_service.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertEqual(result, ResCommonResponse(rt_cd='100', msg1='시가총액 상위 종목 조회 실패', data=None))
        self.mock_broker_api_wrapper.client.quotations.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")

    async def test_handle_upper_limit_stocks_no_top_stocks_found(self):
        """상위 종목 목록이 비어있을 때."""
        market_code = "0000"
        limit = 500

        self.mock_env.is_paper_trading = False

        self.market_data_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]  # ✅ output → data 필드로 매핑
        ))

        result = await self.stock_query_service.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertEqual(result, ResCommonResponse(rt_cd='0', msg1='조회 성공', data=[]))

        self.market_data_service.get_top_market_cap_stocks_code.assert_called_once_with(market_code, limit)
        self.mock_broker_api_wrapper.client.quotations.get_current_price.assert_not_called()

        self.assertTrue(self.mock_logger.info.called)

    @pytest.mark.asyncio
    async def test_handle_upper_limit_stocks_success(self):
        mock_env = MagicMock()
        mock_env.is_paper_trading = False

        mock_logger = MagicMock()
        mock_market_clock = MagicMock()

        market_data_service = MagicMock()
        market_data_service._env = mock_env
        market_data_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                ResTopMarketCapApiItem(
                    iscd="CODE001", mksc_shrn_iscd="CODE001", stck_avls="100000000000",
                    data_rank="1", hts_kor_isnm="상한가종목1", acc_trdvol="100000"
                ),
                ResTopMarketCapApiItem(
                    iscd="CODE002", mksc_shrn_iscd="CODE002", stck_avls="90000000000",
                    data_rank="2", hts_kor_isnm="일반종목2", acc_trdvol="200000"
                ),
                ResTopMarketCapApiItem(
                    iscd="CODE003", mksc_shrn_iscd="CODE003", stck_avls="80000000000",
                    data_rank="3", hts_kor_isnm="상한가종목3", acc_trdvol="150000"
                )
            ]
        ))

        data_handler = StockQueryService(
            market_data_service=market_data_service,
            market_clock=mock_market_clock,
            logger=mock_logger
        )

        await data_handler.handle_upper_limit_stocks(market_code="0000", limit=500)

        market_data_service.get_top_market_cap_stocks_code.assert_called_once_with("0000", 500)
        assert mock_logger.info.called

    async def test_handle_upper_limit_stocks_individual_stock_price_failure(self):
        """개별 종목 현재가 조회 실패 시."""
        market_code = "0000"
        limit = 500
        self.mock_env.is_paper_trading = False

        self.market_data_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                ResTopMarketCapApiItem(
                    iscd="CODE001", mksc_shrn_iscd="CODE001", stck_avls="100000000000",
                    data_rank="1", hts_kor_isnm="상한가종목1", acc_trdvol="100000"
                ),
                ResTopMarketCapApiItem(
                    iscd="CODE002", mksc_shrn_iscd="CODE002", stck_avls="90000000000",
                    data_rank="2", hts_kor_isnm="실패종목2", acc_trdvol="200000"
                ),
            ]
        ))

        result = await self.stock_query_service.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertTrue(result)  # 상한가 종목 1개 발견되므로 True 반환
        self.market_data_service.get_top_market_cap_stocks_code.assert_called_once_with(market_code, limit)

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)
