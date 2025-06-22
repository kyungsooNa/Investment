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


# 테스트용 MockLogger 정의
class MockLogger:
    def __init__(self):
        self.info = mock.Mock()
        self.debug = mock.Mock()
        self.warning = mock.Mock()
        self.error = mock.Mock()
        self.critical = mock.Mock()


class TestTradingService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_env = mock.Mock(spec=KoreaInvestEnv)
        self.mock_env.is_paper_trading = False

        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.AsyncMock(spec=TimeManager)

        self.mock_api_client = mock.AsyncMock(spec=KoreaInvestAPI)

        self.mock_api_client.quotations = mock.AsyncMock(spec=KoreaInvestQuotationsAPI)
        self.mock_api_client.account = mock.AsyncMock(spec=KoreaInvestAccountAPI)
        self.mock_api_client.trading = mock.AsyncMock(spec=KoreaInvestTradingAPI)
        self.mock_api_client.websocket = mock.AsyncMock(spec=KoreaInvestWebSocketAPI)

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
        builtins.print = self.original_print
        self.print_output_capture.close()

    # --- 정상 케이스 ---
    async def test_get_top_10_market_cap_stocks_with_prices_happy_path(self):
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
        self.assertEqual(result[9]['current_price'], "10900")

    # --- 시장이 닫힌 경우 ---
    async def test_get_top_10_market_cap_stocks_when_market_closed(self):
        self.mock_time_manager.is_market_open.return_value = False
        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
        self.assertIsNone(result)

    # --- 모의투자 환경 ---
    async def test_get_top_10_market_cap_stocks_when_paper_trading(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True
        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})

    # --- top stocks API 실패 ---
    async def test_get_top_10_market_cap_stocks_when_top_stocks_api_fails(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "1", "msg1": "API 오류"
        }
        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
        self.assertIsNone(result)

    # --- 빈 목록 반환 ---
    async def test_get_top_10_market_cap_stocks_when_empty_list(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0", "output": []
        }

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
        self.assertIsNone(result)
        self.mock_logger.info.assert_any_call("시가총액 상위 종목 목록을 찾을 수 없습니다.")

    # --- 종목 현재가 일부 실패 ---
    async def test_get_top_10_market_cap_stocks_when_individual_price_fails(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "CODE1", "hts_kor_isnm": "종목1", "data_rank": "1"},
                {"mksc_shrn_iscd": "CODE2", "hts_kor_isnm": "종목2", "data_rank": "2"},
                {"mksc_shrn_iscd": "CODE3", "hts_kor_isnm": "종목3", "data_rank": "3"},
            ]
        }
        self.mock_api_client.quotations.get_current_price.side_effect = [
            {"rt_cd": "0", "output": {"stck_prpr": "10000"}},
            {"rt_cd": "1", "msg1": "조회 실패"},
            {"rt_cd": "0", "output": {"stck_prpr": "30000"}},
        ]

        result = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], "종목1")
        self.assertEqual(result[1]['name'], "종목3")

    # --- 주문 실패 테스트 ---
    async def test_place_buy_order_fail_due_to_lack_of_balance(self):
        # ✅ AsyncMock 으로 응답을 감싸야 await 가능
        self.mock_api_client.trading.place_stock_order = mock.AsyncMock(
            return_value={"rt_cd": "1", "msg1": "주문가능금액 부족"}
        )

        with self.assertRaises(Exception) as context:
            await self.trading_service.place_buy_order("005930", 1000000, 1, "00")

        self.assertIn("주문가능금액 부족", str(context.exception))
