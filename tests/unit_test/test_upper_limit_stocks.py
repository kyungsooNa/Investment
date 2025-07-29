import pytest
import unittest
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock
import sys
from io import StringIO
import builtins

# 테스트할 모듈 임포트
from app.stock_query_service import StockQueryService
from services.trading_service import TradingService
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from core.time_manager import TimeManager  # Mocking용
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
        self.mock_time_manager = mock.MagicMock(spec_set=TimeManager)  # MagicMock으로 변경
        self.mock_time_manager.is_market_open.return_value = True  # 기본값 설정 (시장이 열려있다고 가정)

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
        self.mock_broker_api_wrapper.client.account.get_real_account_balance = mock.AsyncMock()

        self.mock_broker_api_wrapper.client.trading.place_stock_order = mock.AsyncMock()

        # 📌 TradingService 인스턴스 생성 (주입) - setUp에서 한 번만 생성
        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,  # 여기에서 Mock api_client를 주입
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # 📌 DataHandlers 인스턴스 생성 (handle_upper_limit_stocks 포함) - setUp에서 한 번만 생성
        self.data_handlers = StockQueryService(
            trading_service=self.trading_service,  # 여기에서 Mock trading_service를 주입
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # print 함수 출력을 캡처 (콘솔 출력 검증용)
        self.original_print = builtins.print
        self.print_output_capture = StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self.print_output_capture
        builtins.print = lambda *args, **kwargs: self.print_output_capture.write(' '.join(map(str, args)) + '\n')

    def tearDown(self):
        """각 테스트 메서드 실행 후에 설정을 정리합니다."""
        builtins.print = self.original_print
        self.print_output_capture.close()
        sys.stdout = self._original_stdout

    # --- handle_upper_limit_stocks 테스트 케이스들 ---

    async def test_handle_upper_limit_stocks_market_closed(self):
        """시장이 닫혀있을 때 상한가 종목 조회 시도."""
        self.mock_time_manager.is_market_open.return_value = False

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertIsNone(result)
        self.mock_time_manager.is_market_open.assert_called_once()
        # 📌 수정된 경로: self.mock_api_client.quotations
        self.mock_broker_api_wrapper.client.quotations.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.")
        self.assertIn("WARNING: 시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_paper_trading(self):
        """모의투자 환경에서 상한가 종목 조회 시도 (미지원)."""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})
        self.mock_broker_api_wrapper.client.quotations.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("Service - 상한가 종목 조회는 모의투자를 지원하지 않습니다.")
        self.assertIn("WARNING: 모의투자 환경에서는 상한가 종목 조회를 지원하지 않습니다.\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_no_top_stocks_found(self):
        """상위 종목 목록이 비어있을 때."""
        market_code = "0000"
        limit = 500

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={
            "rt_cd": "0", "msg1": "정상", "output": []
        })

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertIsNone(result)

        self.trading_service.get_top_market_cap_stocks_code.assert_called_once_with(market_code)
        self.mock_broker_api_wrapper.client.quotations.get_current_price.assert_not_called()

        self.assertTrue(self.mock_logger.info.called)

    @pytest.mark.asyncio
    async def test_handle_upper_limit_stocks_success(self):
        mock_env = MagicMock()
        mock_env.is_paper_trading = False

        mock_logger = MagicMock()
        mock_time_manager = MagicMock()
        mock_time_manager.is_market_open.return_value = True

        trading_service = MagicMock()
        trading_service._env = mock_env
        trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
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

        trading_service.get_current_stock_price = AsyncMock(side_effect=[
            ResCommonResponse(
                rt_cd="0",
                msg1="정상",
                data={"output": make_stock_payload("1", "10000", "30.0")}
            ),
            ResCommonResponse(
                rt_cd="0",
                msg1="정상",
                data={"output": make_stock_payload("2", "100", "1.0")}
            ),
            ResCommonResponse(
                rt_cd="0",
                msg1="정상",
                data={"output": make_stock_payload("1", "5000", "29.8")}
            ),
        ])

        data_handler = StockQueryService(
            trading_service=trading_service,
            time_manager=mock_time_manager,
            logger=mock_logger
        )

        result = await data_handler.handle_upper_limit_stocks(market_code="0000", limit=500)

        assert result is True
        trading_service.get_top_market_cap_stocks_code.assert_called_once_with("0000")
        assert trading_service.get_current_stock_price.call_count == 3
        assert mock_logger.info.called

    async def test_handle_upper_limit_stocks_individual_stock_price_failure(self):
        """개별 종목 현재가 조회 실패 시."""
        market_code = "0000"
        limit = 500
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
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

        self.trading_service.get_current_stock_price = AsyncMock(side_effect=[
            ResCommonResponse(
                rt_cd="0",
                msg1="정상",
                data={"output": make_stock_payload("1", "10000", "30.0")}
            ),
            ResCommonResponse(
                rt_cd="1",
                msg1="조회 실패",
                data=None
            )
        ])

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertTrue(result)  # 상한가 종목 1개 발견되므로 True 반환
        self.trading_service.get_top_market_cap_stocks_code.assert_called_once_with(market_code)
        self.assertEqual(self.trading_service.get_current_stock_price.call_count, 2)

        # 콘솔 출력 검증
        output = self.print_output_capture.getvalue()
        self.assertIn("상한가종목1", output)
        self.assertIn("CODE001", output)
        self.assertIn("등락률", output)

        # logger.warning이 한 번 이상 호출되었는지만 확인
        self.assertTrue(self.mock_logger.warning.called)

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)
