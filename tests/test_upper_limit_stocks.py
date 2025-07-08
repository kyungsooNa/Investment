import pytest
import unittest
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import logging
from io import StringIO
import builtins

# 테스트할 모듈 임포트
from app.data_handlers import DataHandlers
from services.trading_service import TradingService
from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
from core.time_manager import TimeManager  # Mocking용
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # Mocking용

# 로거의 출력을 캡처하기 위한 설정 (테스트 시 실제 파일에 로그를 남기지 않도록)
logging.getLogger('operational_logger').propagate = False
logging.getLogger('debug_logger').propagate = False
logging.getLogger('operational_logger').handlers = []
logging.getLogger('debug_logger').handlers = []


# 테스트를 위한 MockLogger (실제 로거 대신 사용)
class MockLogger:
    def __init__(self):
        self.info = mock.Mock()
        self.debug = mock.Mock()
        self.warning = mock.Mock()
        self.error = mock.Mock()
        self.critical = mock.Mock()


class TestUpperLimitStocks(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """각 테스트 메서드 실행 전에 필요한 Mock 객체와 핸들러 인스턴스를 설정합니다."""
        # 종속성 Mock 객체 생성
        self.mock_env = mock.MagicMock(spec=KoreaInvestApiEnv) # MagicMock으로 변경
        self.mock_env.is_paper_trading = False  # 기본값 설정
        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.MagicMock(spec_set=TimeManager) # MagicMock으로 변경
        self.mock_time_manager.is_market_open.return_value = True  # 기본값 설정 (시장이 열려있다고 가정)

        # KoreaInvestApiClient Mocking:
        self.mock_api_client = mock.MagicMock() # MagicMock()만 사용
        self.mock_api_client.quotations = mock.MagicMock(spec_set=KoreaInvestApiQuotations)
        self.mock_api_client.account = mock.MagicMock(spec_set=KoreaInvestApiAccount)
        self.mock_api_client.trading = mock.MagicMock(spec_set=KoreaInvestApiTrading)

        # 각 하위 Mock 객체의 메서드들을 직접 Mock 객체로 할당하고 return_value를 설정합니다.
        # 이렇게 하면 TradingService가 이 Mock 메서드들을 호출할 수 있습니다.
        self.mock_api_client.quotations.get_current_price = mock.AsyncMock() # KoreaInvestApiQuotations의 메서드
        self.mock_api_client.quotations.get_top_market_cap_stocks_code = mock.AsyncMock() # KoreaInvestApiQuotations의 메서드

        self.mock_api_client.account.get_account_balance = mock.AsyncMock()
        self.mock_api_client.account.get_real_account_balance = mock.AsyncMock()

        self.mock_api_client.trading.place_stock_order = mock.AsyncMock()

        # 📌 TradingService 인스턴스 생성 (주입) - setUp에서 한 번만 생성
        self.trading_service = TradingService(
            api_client=self.mock_api_client, # 여기에서 Mock api_client를 주입
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # 📌 DataHandlers 인스턴스 생성 (handle_upper_limit_stocks 포함) - setUp에서 한 번만 생성
        self.data_handlers = DataHandlers(
            trading_service=self.trading_service, # 여기에서 Mock trading_service를 주입
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
        self.mock_api_client.quotations.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.")
        self.assertIn("WARNING: 시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_paper_trading(self):
        """모의투자 환경에서 상한가 종목 조회 시도 (미지원)."""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})
        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.KoreaInvestApiQuotations.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("Service - 상한가 종목 조회는 모의투자를 지원하지 않습니다.")
        self.assertIn("WARNING: 모의투자 환경에서는 상한가 종목 조회를 지원하지 않습니다.\n", self.print_output_capture.getvalue())

        # 📌 수정된 테스트: test_handle_upper_limit_stocks_get_top_market_cap_stocks_failure
        async def test_handle_upper_limit_stocks_get_top_market_cap_stocks_failure(self):
            """시가총액 상위 종목 목록 조회 API 실패 시, 콘솔 및 로그 출력 포함."""

            self.mock_time_manager.is_market_open.return_value = True
            self.mock_env.is_paper_trading = False

            # --- 핵심: TradingService가 호출할 api_client.quotations를 Mock ---
            mock_api_response = {
                "rt_cd": "1",  # 실패 응답
                "msg1": "API 오류"
            }
            # setUp에서 이미 생성된 self.mock_api_client.quotations (MagicMock)의 메서드를 설정
            self.mock_api_client.quotations.get_top_market_cap_stocks_code.return_value = mock_api_response

            # When
            # builtins.print를 patch하는 with 문 제거. sys.stdout 리다이렉션을 사용.
            result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

            # Then
            self.assertIsNone(result)
            self.mock_logger.error.assert_called_once()
            self.mock_logger.error.assert_called_with(f"시가총액 상위 종목 목록 조회 실패: {mock_api_response}")

            # self.trading_service가 내부적으로 호출하는 Mock 메서드에 대한 assert
            self.mock_api_client.quotations.get_top_market_cap_stocks_code.assert_awaited_once_with("0000")

            # 콘솔 출력 메시지 검증
            output = self.print_output_capture.getvalue()
            self.assertIn("실패: 시가총액 상위 종목 목록을 가져올 수 없습니다.", output)
            self.assertIn("API 오류", output)
            self.assertIn("--- 시가총액 상위 500개 종목 중 상한가 종목 조회 ---", output)

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
        self.mock_api_client.KoreaInvestApiQuotations.get_current_price.assert_not_called()

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
        trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE001", "hts_kor_isnm": "상한가종목1", "data_rank": "1"},
                {"mksc_shrn_iscd": "CODE002", "hts_kor_isnm": "일반종목2", "data_rank": "2"},
                {"mksc_shrn_iscd": "CODE003", "hts_kor_isnm": "상한가종목3", "data_rank": "3"},
            ]
        })

        trading_service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "10000", "prdy_ctrt": "30.0"}},  # 상한가
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "2", "stck_prpr": "100", "prdy_ctrt": "1.0"}},  # 일반
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "5000", "prdy_ctrt": "29.8"}},  # 상한가
        ])

        from app.data_handlers import DataHandlers

        data_handler = DataHandlers(
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
        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value={
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE001", "hts_kor_isnm": "상한가종목1", "data_rank": "1"},
                {"mksc_shrn_iscd": "CODE002", "hts_kor_isnm": "실패종목2", "data_rank": "2"},
            ]
        })
        self.trading_service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "10000", "prdy_ctrt": "30.0"}},
            {"rt_cd": "1", "msg1": "조회 실패"},
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
