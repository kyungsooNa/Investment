import unittest
import unittest.mock as mock
import asyncio
import datetime
import logging
from io import StringIO
import builtins
import pytz

# 테스트할 모듈 임포트
from services.trading_service import TradingService
from api.client import KoreaInvestAPI
from api.quotations import KoreaInvestQuotationsAPI
from api.account import KoreaInvestAccountAPI
from api.trading import KoreaInvestTradingAPI
from api.websocket_client import KoreaInvestWebSocketAPI
from core.time_manager import TimeManager
from core.logger import Logger
from api.env import KoreaInvestEnv

# 로거의 출력을 캡처하기 위한 설정
logging.getLogger('operational_logger').propagate = True
logging.getLogger('debug_logger').propagate = True
logging.getLogger('operational_logger').handlers = []
logging.getLogger('debug_logger').handlers = []


# 테스트를 위한 Logger 인스턴스 목킹
class MockLogger:
    def __init__(self):
        self.info = mock.Mock()
        self.debug = mock.Mock()
        self.warning = mock.Mock()
        self.error = mock.Mock()
        self.critical = mock.Mock()


# 테스트 클래스 정의
class TestTradingService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """각 테스트 메서드 실행 전에 필요한 목 객체와 인스턴스를 설정합니다."""
        # 종속성 목(Mock) 객체 생성
        self.mock_env = mock.Mock(spec=KoreaInvestEnv)
        self.mock_env.is_paper_trading = False

        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.AsyncMock(spec_set=TimeManager)

        self.mock_api_client = mock.AsyncMock(spec=KoreaInvestAPI)

        self.mock_api_client.quotations = mock.AsyncMock(spec_set=KoreaInvestQuotationsAPI)
        self.mock_api_client.account = mock.AsyncMock(spec_set=KoreaInvestAccountAPI)
        self.mock_api_client.trading = mock.AsyncMock(spec_set=KoreaInvestTradingAPI)
        self.mock_api_client.websocket = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI)

        self.mock_api_client.quotations.get_current_price = mock.AsyncMock(
            spec_set=KoreaInvestQuotationsAPI.get_current_price)
        self.mock_api_client.quotations.get_top_market_cap_stocks = mock.AsyncMock(
            spec_set=KoreaInvestQuotationsAPI.get_top_market_cap_stocks)

        self.mock_api_client.account.get_account_balance = mock.AsyncMock(
            spec_set=KoreaInvestAccountAPI.get_account_balance)
        self.mock_api_client.account.get_real_account_balance = mock.AsyncMock(
            spec_set=KoreaInvestAccountAPI.get_real_account_balance)

        self.mock_api_client.trading.place_stock_order = mock.AsyncMock(
            spec_set=KoreaInvestTradingAPI.place_stock_order)

        self.mock_api_client.websocket.connect = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.connect)
        self.mock_api_client.websocket.disconnect = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.disconnect)
        self.mock_api_client.websocket.subscribe_realtime_price = mock.AsyncMock(
            spec_set=KoreaInvestWebSocketAPI.subscribe_realtime_price)
        self.mock_api_client.websocket.unsubscribe_realtime_price = mock.AsyncMock(
            spec_set=KoreaInvestWebSocketAPI.unsubscribe_realtime_price)
        self.mock_api_client.websocket.subscribe_realtime_quote = mock.AsyncMock(
            spec_set=KoreaInvestWebSocketAPI.subscribe_realtime_quote)
        self.mock_api_client.websocket.unsubscribe_realtime_quote = mock.AsyncMock(
            spec_set=KoreaInvestWebSocketAPI.unsubscribe_realtime_quote)

        self.trading_service = TradingService(
            api_client=self.mock_api_client,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        self.original_print = builtins.print
        self.print_output_capture = StringIO()
        builtins.print = lambda *args, **kwargs: self.print_output_capture.write(' '.join(map(str, args)) + '\n')

    def tearDown(self):
        """각 테스트 메서드 실행 후에 설정을 정리합니다."""
        builtins.print = self.original_print
        self.print_output_capture.close()

    # --- 1. 정상 작동 테스트 (Happy Path) ---
    async def test_get_top_10_market_cap_stocks_with_prices_happy_path(self):
        """
        시장 개장, 실전투자 환경, 모든 API 호출 성공 시 상위 10개 종목 현재가 조회.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": f"CODE{i}", "hts_kor_isnm": f"종목명{i}", "data_rank": str(i + 1),
                 "stck_avls": f"시총{i}"}
                for i in range(10)
            ]
        }
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"stck_prpr": str(10000 + i * 100)}} for i in range(10)
        ]

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0]['name'], "종목명0")
        self.assertEqual(result[0]['current_price'], "10000")
        self.assertEqual(result[9]['name'], "종목명9")
        self.assertEqual(result[9]['current_price'], "10900")

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with("0000")
        self.assertEqual(self.mock_api_client.quotations.get_current_price.call_count, 10)

        self.mock_logger.info.assert_any_call("Service - 시가총액 1~10위 종목 현재가 조회 요청")
        # --- 수정: 해당 로그 메시지는 더 이상 출력되지 않으므로 제거하거나 다른 적절한 로그로 대체 ---
        # self.mock_logger.info.assert_any_call("시장이 열렸습니다. 시가총액 1~10위 종목 현재가 조회를 시작합니다.")
        # --------------------------------------------------------------------------------------
        self.mock_logger.info.assert_any_call("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")

    # --- 2.1: 시장이 폐장했을 때 ---
    async def test_get_top_10_market_cap_stocks_when_market_closed(self):
        """
        시장이 닫혀있을 때 시가총액 상위 종목 현재가 조회 시도.
        """
        self.mock_time_manager.is_market_open.return_value = False

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNone(result)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_time_manager.get_next_market_open_time.assert_not_called()
        self.mock_time_manager.async_sleep.assert_not_called()

        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_not_called()
        self.mock_api_client.quotations.get_current_price.assert_not_called()

        self.mock_logger.info.assert_any_call("Service - 시가총액 1~10위 종목 현재가 조회 요청")
        self.mock_logger.warning.assert_any_call("시장이 닫혀 있어 시가총액 1~10위 종목 현재가 조회를 수행할 수 없습니다.")

    # --- 2.2: 모의투자 환경에서 호출했을 때 (미지원 API) ---
    async def test_get_top_10_market_cap_stocks_when_paper_trading(self):
        """
        모의투자 환경에서 시가총액 상위 종목 현재가 조회 시도 (미지원).
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_not_called()
        self.mock_api_client.quotations.get_current_price.assert_not_called()

        self.mock_logger.info.assert_any_call("Service - 시가총액 1~10위 종목 현재가 조회 요청")
        self.mock_logger.warning.assert_any_call("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")

    # --- 2.3: get_top_market_cap_stocks API 호출이 실패했을 때 ---
    async def test_get_top_10_market_cap_stocks_when_top_stocks_api_fails(self):
        """
        시가총액 상위 종목 목록 조회 API 호출이 실패했을 때.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {"rt_cd": "1", "msg1": "API 오류"}

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNone(result)

        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with("0000")
        self.mock_api_client.quotations.get_current_price.assert_not_called()

        self.mock_logger.error.assert_any_call(mock.ANY)

        # --- 2.4: get_top_market_cap_stocks가 빈 목록을 반환했을 때 ---

    async def test_get_top_10_market_cap_stocks_when_empty_list(self):
        """
        시가총액 상위 종목 목록 조회 API가 성공하지만 빈 목록을 반환했을 때.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {"rt_cd": "0", "output": []}

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        self.assertIsNone(result)

        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with("0000")
        self.mock_api_client.quotations.get_current_price.assert_not_called()

        self.mock_logger.info.assert_any_call("시가총액 상위 종목 목록을 찾을 수 없습니다.")

    # --- 2.5: 개별 종목 get_current_stock_price 호출이 실패했을 때 ---
    async def test_get_top_10_market_cap_stocks_when_individual_price_fails(self):
        """
        상위 10개 종목 중 일부 또는 전체의 현재가 조회 API 호출이 실패했을 때.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        # 3개의 종목 중 2개는 성공, 1개는 실패하도록 설정
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE1", "hts_kor_isnm": "종목1", "data_rank": "1"},
                {"mksc_shrn_iscd": "CODE2", "hts_kor_isnm": "종목2", "data_rank": "2"},
                {"mksc_shrn_iscd": "CODE3", "hts_kor_isnm": "종목3", "data_rank": "3"},
            ]
        }
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"stck_prpr": "10000"}},  # 종목1 성공
            {"rt_cd": "1", "msg1": "조회 실패"},  # 종목2 실패
            {"rt_cd": "0", "output": {"stck_prpr": "30000"}}  # 종목3 성공
        ]

        # 함수 실행
        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        # 결과 검증: 실패한 종목은 제외되어야 함
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)  # 2개만 성공
        self.assertEqual(result[0]['name'], "종목1")
        self.assertEqual(result[1]['name'], "종목3")

        # Mock 호출 검증
        self.assertEqual(self.mock_api_client.quotations.get_current_price.call_count, 3)

        # 로그 검증: 실패 로그 확인
        self.mock_logger.error.assert_any_call(f"종목 CODE2 (종목2) 현재가 조회 실패: {{'rt_cd': '1', 'msg1': '조회 실패'}}")
        self.mock_logger.info.assert_any_call("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")

# 이 테스트 파일을 실행하는 방법:
# 1. 터미널에서 PyCharmMiscProject/tests/ 디렉토리로 이동합니다.
# 2. python -m unittest test_trading_service.py 를 실행합니다.
#    또는 PyCharm에서 해당 파일을 열고 Run 버튼을 클릭합니다.
