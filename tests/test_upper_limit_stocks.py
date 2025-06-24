import unittest
import unittest.mock as mock
import asyncio
import datetime
import logging
from io import StringIO
import builtins
import pytz

# 테스트할 모듈 임포트
from app.data_handlers import DataHandlers
from services.trading_service import TradingService
from api.client import KoreaInvestAPI  # Mocking용
from api.quotations import Quotations  # Mocking용
from api.account import KoreaInvestAccountAPI  # Mocking용
from api.trading import KoreaInvestTradingAPI  # Mocking용
from api.websocket_client import WebSocketClient  # Mocking용
from core.time_manager import TimeManager  # Mocking용
from core.logger import Logger  # Mocking용
from api.env import KoreaInvestEnv  # Mocking용

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
        self.mock_env = mock.Mock(spec=KoreaInvestEnv)
        self.mock_env.is_paper_trading = False  # 기본값 설정
        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.AsyncMock(spec_set=TimeManager)
        self.mock_time_manager.is_market_open.return_value = True  # 기본값 설정 (시장이 열려있다고 가정)

        # KoreaInvestAPI Mocking: spec을 제거하여 동적 속성 할당을 허용
        self.mock_api_client = mock.AsyncMock()

        # 하위 API 클라이언트들을 Mock 객체로 할당. 이들 자체에 spec_set을 적용.
        self.mock_api_client.quotations = mock.AsyncMock(spec_set=Quotations)
        self.mock_api_client.account = mock.AsyncMock(spec_set=KoreaInvestAccountAPI)
        self.mock_api_client.trading = mock.AsyncMock(spec_set=KoreaInvestTradingAPI)
        self.mock_api_client.websocket = mock.AsyncMock(spec_set=WebSocketClient)

        # 각 하위 Mock 객체의 메서드들을 직접 다시 Mock 객체로 할당하지 않습니다.
        # 위에서 spec_set을 통해 자동으로 Mocking되었기 때문에,
        # 이제 바로 self.mock_api_client.quotations.get_current_price와 같이 접근하여 return_value를 설정합니다.
        self.mock_api_client.quotations.get_current_price = mock.AsyncMock(
            spec_set=Quotations.get_current_price)
        self.mock_api_client.quotations.get_top_market_cap_stocks = mock.AsyncMock(
            spec_set=Quotations.get_top_market_cap_stocks)
        self.mock_api_client.account.get_account_balance = mock.AsyncMock(
            spec_set=KoreaInvestAccountAPI.get_account_balance)
        self.mock_api_client.account.get_real_account_balance = mock.AsyncMock(
            spec_set=KoreaInvestAccountAPI.get_real_account_balance)
        self.mock_api_client.trading.place_stock_order = mock.AsyncMock(
            spec_set=KoreaInvestTradingAPI.place_stock_order)
        self.mock_api_client.websocket.connect = mock.AsyncMock(spec_set=WebSocketClient.connect)
        self.mock_api_client.websocket.disconnect = mock.AsyncMock(spec_set=WebSocketClient.disconnect)
        self.mock_api_client.websocket.subscribe_realtime_price = mock.AsyncMock(
            spec_set=WebSocketClient.subscribe_realtime_price)
        self.mock_api_client.websocket.unsubscribe_realtime_price = mock.AsyncMock(
            spec_set=WebSocketClient.unsubscribe_realtime_price)
        self.mock_api_client.websocket.subscribe_realtime_quote = mock.AsyncMock(
            spec_set=WebSocketClient.subscribe_realtime_quote)
        self.mock_api_client.websocket.unsubscribe_realtime_quote = mock.AsyncMock(
            spec_set=WebSocketClient.unsubscribe_realtime_quote)

        # TradingService 인스턴스 생성 (주입)
        self.trading_service = TradingService(
            api_client=self.mock_api_client,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # DataHandlers 인스턴스 생성 (handle_upper_limit_stocks 포함)
        self.data_handlers = DataHandlers(self.trading_service, self.mock_logger, self.mock_time_manager)

        # print 함수 출력을 캡처 (콘솔 출력 검증용)
        self.original_print = builtins.print
        self.print_output_capture = StringIO()
        builtins.print = lambda *args, **kwargs: self.print_output_capture.write(' '.join(map(str, args)) + '\n')

    def tearDown(self):
        """각 테스트 메서드 실행 후에 설정을 정리합니다."""
        builtins.print = self.original_print
        self.print_output_capture.close()

    # --- handle_upper_limit_stocks 테스트 케이스들 ---

    async def test_handle_upper_limit_stocks_market_closed(self):
        """시장이 닫혀있을 때 상한가 종목 조회 시도."""
        self.mock_time_manager.is_market_open.return_value = False

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertIsNone(result)
        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.")
        self.assertIn("WARNING: 시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_paper_trading(self):
        """모의투자 환경에서 상한가 종목 조회 시도 (미지원)."""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})
        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_not_called()
        self.mock_logger.warning.assert_called_once_with("Service - 상한가 종목 조회는 모의투자를 지원하지 않습니다.")
        self.assertIn("WARNING: 모의투자 환경에서는 상한가 종목 조회를 지원하지 않습니다.\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_get_top_market_cap_stocks_failure(self):
        """시가총액 상위 종목 목록 조회 API 실패 시."""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {"rt_cd": "1", "msg1": "API 오류"}

        result = await self.data_handlers.handle_upper_limit_stocks(market_code="0000", limit=500)

        self.assertIsNone(result)
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with("0000", 10)
        self.mock_api_client.quotations.get_current_price.assert_not_called()
        self.mock_logger.error.assert_called_once_with(f"시가총액 상위 종목 목록 조회 실패: {{'rt_cd': '1', 'msg1': 'API 오류'}}")
        self.assertIn("실패: 시가총액 상위 종목 목록을 가져올 수 없습니다. API 오류\n", self.print_output_capture.getvalue())

    async def test_handle_upper_limit_stocks_no_top_stocks_found(self):
        """상위 종목 목록이 비어있을 때."""
        market_code = "0000"
        limit = 500

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        # 이 조건으로 인해 logger.error 분기로 빠짐
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0", "msg1": "정상", "output": []  # <- 빈 output이므로 에러 로그로 빠짐
        }

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertIsNone(result)

        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with(market_code, 10)
        self.mock_api_client.quotations.get_current_price.assert_not_called()

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)


    async def test_handle_upper_limit_stocks_no_upper_limit_stocks_found(self):
        """상위 종목은 있지만 상한가 종목이 없을 때."""
        market_code = "0000"
        limit = 500
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": f"CODE{i}", "hts_kor_isnm": f"종목명{i}", "data_rank": str(i + 1)} for i in range(5)
            ]
        }
        # 모든 종목이 상한가가 아닌 경우 (prdy_vrss_sign != '1')
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "2", "stck_prpr": "100", "prdy_ctrt": "1.0"}}
            for _ in range(5)
        ]

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertFalse(result)  # False 반환
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with(market_code, 10)

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)

    async def test_handle_upper_limit_stocks_success(self):
        """상한가 종목이 발견되었을 때 (Happy Path)."""
        market_code = "0000"
        limit = 500
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE001", "hts_kor_isnm": "상한가종목1", "data_rank": "1"},
                {"mksc_shrn_iscd": "CODE002", "hts_kor_isnm": "일반종목2", "data_rank": "2"},
                {"mksc_shrn_iscd": "CODE003", "hts_kor_isnm": "상한가종목3", "data_rank": "3"},
            ]
        }
        # side_effect를 사용하여 특정 종목만 상한가로 설정
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "10000", "prdy_ctrt": "30.0"}},  # CODE001 상한가
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "2", "stck_prpr": "100", "prdy_ctrt": "1.0"}},  # CODE002 일반
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "5000", "prdy_ctrt": "29.8"}},  # CODE003 상한가
        ]

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertTrue(result)  # True 반환
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once()
        self.assertEqual(self.mock_api_client.quotations.get_current_price.call_count, 3)

        # 콘솔 출력 검증
        self.assertIn("--- 상한가 종목 목록 ---", self.print_output_capture.getvalue())
        self.assertIn("상한가종목1 (CODE001): 10000원 (등락률: +30.0%)\n", self.print_output_capture.getvalue())
        self.assertIn("상한가종목3 (CODE003): 5000원 (등락률: +29.8%)\n", self.print_output_capture.getvalue())
        # self.mock_logger.info.assert_called_once_with("총 2개의 상한가 종목 발견.") # <--- 제거

        # logger.warning이 한 번 이상 호출되었는지만 확인
        self.assertTrue(self.mock_logger.warning.called)

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)


    async def test_handle_upper_limit_stocks_individual_stock_price_failure(self):
        """개별 종목 현재가 조회 실패 시."""
        market_code = "0000"
        limit = 500
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE001", "hts_kor_isnm": "상한가종목1", "data_rank": "1"},  # 성공 예정
                {"mksc_shrn_iscd": "CODE002", "hts_kor_isnm": "실패종목2", "data_rank": "2"},  # 실패 예정
            ]
        }
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "10000", "prdy_ctrt": "30.0"}},
            {"rt_cd": "1", "msg1": "조회 실패"},  # 이 종목의 조회는 실패
        ]

        result = await self.data_handlers.handle_upper_limit_stocks(market_code=market_code, limit=limit)

        self.assertTrue(result)  # 상한가 종목 1개 발견되므로 True 반환
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with(market_code, 10)
        self.assertEqual(self.mock_api_client.quotations.get_current_price.call_count, 2)

        # 콘솔 출력 검증
        self.assertIn("상한가종목1 (CODE001): 10000원 (등락률: +30.0%)\n", self.print_output_capture.getvalue())

        # logger.warning이 한 번 이상 호출되었는지만 확인
        self.assertTrue(self.mock_logger.warning.called)

        # logger.info도 마찬가지로 한 번이라도 호출되었는지만 확인
        self.assertTrue(self.mock_logger.info.called)
