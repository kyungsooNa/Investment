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
from app.transaction_handlers import TransactionHandlers
from services.trading_service import TradingService
from api.client import KoreaInvestAPI  # Mocking용
from api.quotations import KoreaInvestQuotationsAPI  # Mocking용
from api.account import KoreaInvestAccountAPI  # Mocking용
from api.trading import KoreaInvestTradingAPI  # Mocking용
from api.websocket_client import KoreaInvestWebSocketAPI  # Mocking용
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


class TestAppHandlers(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """각 테스트 메서드 실행 전에 필요한 Mock 객체와 핸들러 인스턴스를 설정합니다."""
        # 종속성 목(Mock) 객체 생성
        self.mock_env = mock.Mock(spec=KoreaInvestEnv)
        self.mock_env.is_paper_trading = False  # 기본값 설정
        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.AsyncMock(spec_set=TimeManager)
        self.mock_time_manager.is_market_open.return_value = True  # 기본값 설정 (시장이 열려있다고 가정)

        # KoreaInvestAPI Mocking: spec을 제거하여 동적 속성 할당을 허용
        self.mock_api_client = mock.AsyncMock()

        # 하위 API 클라이언트들을 Mock 객체로 할당. 이들 자체에 spec_set을 적용.
        self.mock_api_client.quotations = mock.AsyncMock(spec_set=KoreaInvestQuotationsAPI)
        self.mock_api_client.account = mock.AsyncMock(spec_set=KoreaInvestAccountAPI)
        self.mock_api_client.trading = mock.AsyncMock(spec_set=KoreaInvestTradingAPI)
        self.mock_api_client.websocket = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI)

        # 각 하위 Mock 객체의 메서드들을 직접 다시 Mock 객체로 할당하지 않습니다.
        # 위에서 spec_set을 통해 자동으로 Mocking되었기 때문에,
        # 이제 바로 self.mock_api_client.quotations.get_current_price와 같이 접근하여 return_value를 설정합니다.
        # (이전의 중복 할당 라인들 제거)
        # self.mock_api_client.quotations.get_current_price = mock.AsyncMock(spec_set=KoreaInvestQuotationsAPI.get_current_price)
        # self.mock_api_client.quotations.get_top_market_cap_stocks = mock.AsyncMock(spec_set=KoreaInvestQuotationsAPI.get_top_market_cap_stocks)
        # self.mock_api_client.account.get_account_balance = mock.AsyncMock(spec_set=KoreaInvestAccountAPI.get_account_balance)
        # self.mock_api_client.account.get_real_account_balance = mock.AsyncMock(spec_set=KoreaInvestAccountAPI.get_real_account_balance)
        # self.mock_api_client.trading.place_stock_order = mock.AsyncMock(spec_set=KoreaInvestTradingAPI.place_stock_order)
        # self.mock_api_client.websocket.connect = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.connect)
        # self.mock_api_client.websocket.disconnect = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.disconnect)
        # self.mock_api_client.websocket.subscribe_realtime_price = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.subscribe_realtime_price)
        # self.mock_api_client.websocket.unsubscribe_realtime_price = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.unsubscribe_realtime_price)
        # self.mock_api_client.websocket.subscribe_realtime_quote = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.subscribe_realtime_quote)
        # self.mock_api_client.websocket.unsubscribe_realtime_quote = mock.AsyncMock(spec_set=KoreaInvestWebSocketAPI.unsubscribe_realtime_quote)

        # TradingService 인스턴스 생성 (주입)
        self.trading_service = TradingService(
            api_client=self.mock_api_client,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # DataHandlers와 TransactionHandlers 인스턴스 생성
        self.data_handlers = DataHandlers(self.trading_service, self.mock_logger, self.mock_time_manager)
        self.transaction_handlers = TransactionHandlers(self.trading_service, self.mock_logger, self.mock_time_manager)

        # print 함수 출력을 캡처 (콘솔 출력 검증용)
        self.original_print = builtins.print
        self.print_output_capture = StringIO()
        builtins.print = lambda *args, **kwargs: self.print_output_capture.write(' '.join(map(str, args)) + '\n')

    def tearDown(self):
        """각 테스트 메서드 실행 후에 설정을 정리합니다."""
        builtins.print = self.original_print
        self.print_output_capture.close()

    # --- DataHandlers (메뉴 1, 2, 5, 6, 7, 8 에 해당) ---

    # 메뉴 1: 주식 현재가 조회 (삼성전자) - handle_get_current_stock_price
    async def test_handle_get_current_stock_price_success(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {
            "rt_cd": "0", "msg1": "정상", "output": {"stck_prpr": "70000"}
        }

        await self.data_handlers.handle_get_current_stock_price(stock_code)

        self.mock_api_client.quotations.get_current_price.assert_called_once_with(stock_code)
        self.assertIn(f"--- {stock_code} 현재가 조회 ---", self.print_output_capture.getvalue())
        # --- 수정된 부분: 실제 출력 문자열에 맞게 변경 ---
        self.assertIn(f"{stock_code} 현재가: {{'rt_cd': '0', 'msg1': '정상', 'output': {{'stck_prpr': '70000'}}}}",
                      self.print_output_capture.getvalue())
        # -----------------------------------------------

        self.mock_logger.info.assert_has_calls([
            mock.call(f"Service - {stock_code} 현재가 조회 요청"),
            mock.call(f"{stock_code} 현재가 조회 성공: {{'rt_cd': '0', 'msg1': '정상', 'output': {{'stck_prpr': '70000'}}}}")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_get_current_stock_price_failure(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {
            "rt_cd": "1", "msg1": "오류 발생"
        }

        await self.data_handlers.handle_get_current_stock_price(stock_code)

        self.mock_api_client.quotations.get_current_price.assert_called_once_with(stock_code)
        self.assertIn(f"--- {stock_code} 현재가 조회 ---", self.print_output_capture.getvalue())
        self.assertIn(f"실패: {stock_code} 현재가 조회.", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_called_once_with(f"Service - {stock_code} 현재가 조회 요청")
        self.mock_logger.error.assert_called_once()

        # 메뉴 2: 계좌 잔고 조회 - handle_get_account_balance

    async def test_handle_get_account_balance_success_paper(self):
        self.mock_env.is_paper_trading = True
        self.mock_api_client.account.get_account_balance.return_value = {
            "rt_cd": "0", "msg1": "정상", "output1": [], "output2": [{"dnca_tot_amt": "1000000"}]
        }

        await self.data_handlers.handle_get_account_balance()

        self.mock_api_client.account.get_account_balance.assert_called_once()
        self.assertIn("--- 계좌 잔고 조회 ---", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call("Service - 계좌 잔고 조회 요청 (환경: 모의투자)"),
            mock.call(
                "계좌 잔고 조회 성공: {'rt_cd': '0', 'msg1': '정상', 'output1': [], 'output2': [{'dnca_tot_amt': '1000000'}]}")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_get_account_balance_success_real(self):
        self.mock_env.is_paper_trading = False
        self.mock_api_client.account.get_real_account_balance.return_value = {
            "rt_cd": "0", "msg1": "정상", "output1": [], "output2": [{"dnca_tot_amt": "5000000"}]
        }

        await self.data_handlers.handle_get_account_balance()

        self.mock_api_client.account.get_real_account_balance.assert_called_once()
        self.assertIn("--- 계좌 잔고 조회 ---", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call("Service - 계좌 잔고 조회 요청 (환경: 실전)"),
            mock.call(
                "계좌 잔고 조회 성공: {'rt_cd': '0', 'msg1': '정상', 'output1': [], 'output2': [{'dnca_tot_amt': '5000000'}]}")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_get_account_balance_failure(self):
        self.mock_api_client.account.get_account_balance.return_value = {"rt_cd": "1", "msg1": "조회 실패"}
        self.mock_env.is_paper_trading = True # 모의 환경으로 설정하여 get_account_balance가 호출되도록

        await self.data_handlers.handle_get_account_balance()

        self.mock_api_client.account.get_account_balance.assert_called_once()
        self.assertIn("\n계좌 잔고 조회 실패.\n", self.print_output_capture.getvalue()) # <--- 수정됨
        self.mock_logger.info.assert_called_once_with("Service - 계좌 잔고 조회 요청 (환경: 모의투자)")
        self.mock_logger.error.assert_called_once()

    # 메뉴 5: 주식 전일대비 등락률 조회 - handle_display_stock_change_rate
    async def test_handle_display_stock_change_rate_success(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {
            "rt_cd": "0", "msg1": "정상", "output": {
                "stck_prpr": "70000", "prdy_vrss": "500", "prdy_vrss_sign": "2", "prdy_ctrt": "0.72"
            }
        }
        await self.data_handlers.handle_display_stock_change_rate(stock_code)

        self.assertIn(f"--- {stock_code} 전일대비 등락률 조회 ---", self.print_output_capture.getvalue())
        self.assertIn(f"성공: {stock_code} (70000원)", self.print_output_capture.getvalue())
        self.assertIn("전일대비: +500원", self.print_output_capture.getvalue())
        self.assertIn("전일대비율: 0.72%", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call(f"Service - {stock_code} 현재가 조회 요청"),
            mock.call(f"{stock_code} 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=+500, 등락률=0.72%")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_display_stock_change_rate_failure(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {"rt_cd": "1", "msg1": "조회 실패"}

        await self.data_handlers.handle_display_stock_change_rate(stock_code)

        self.assertIn(f"실패: {stock_code} 전일대비 등락률 조회.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once_with(f"Service - {stock_code} 현재가 조회 요청")
        self.mock_logger.error.assert_called_once()

        # 메뉴 6: 주식 시가대비 조회 - handle_display_stock_vs_open_price

    async def test_handle_display_stock_vs_open_price_success(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {
            "rt_cd": "0", "msg1": "정상", "output": {
                "stck_prpr": "70000", "stck_oprc": "69000", "oprc_vrss_prpr_sign": "2"
            }
        }
        await self.data_handlers.handle_display_stock_vs_open_price(stock_code)

        self.assertIn(f"--- {stock_code} 시가대비 조회 ---", self.print_output_capture.getvalue())
        self.assertIn(f"성공: {stock_code}", self.print_output_capture.getvalue())
        self.assertIn("현재가: 70000원", self.print_output_capture.getvalue())
        self.assertIn("시가: 69000원", self.print_output_capture.getvalue())
        self.assertIn("시가대비 등락률: +1000원 (+1.45%)", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call(f"Service - {stock_code} 현재가 조회 요청"),
            mock.call(f"{stock_code} 시가대비 조회 성공: 현재가=70000, 시가=69000, 시가대비=+1000원 (+1.45%)")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_display_stock_vs_open_price_failure(self):
        stock_code = "005930"
        self.mock_api_client.quotations.get_current_price.return_value = {"rt_cd": "1", "msg1": "조회 실패"}

        await self.data_handlers.handle_display_stock_vs_open_price(stock_code)

        self.assertIn(f"실패: {stock_code} 시가대비 조회.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once_with(f"Service - {stock_code} 현재가 조회 요청")
        self.mock_logger.error.assert_called_once()

        # --- TransactionHandlers (메뉴 3, 4 에 해당) ---

    # 메뉴 3: 주식 매수 주문 - handle_place_buy_order
    async def test_handle_place_buy_order_market_open_success(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_api_client.trading.place_stock_order.return_value = {"rt_cd": "0", "msg1": "주문 성공"}

        await self.transaction_handlers.handle_place_buy_order(stock_code, price, qty, order_dvsn)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.trading.place_stock_order.assert_called_once_with(stock_code, price, qty, "매수", order_dvsn)
        self.assertIn("주식 매수 주문 성공:", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once()

    async def test_handle_place_buy_order_market_closed(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = False

        await self.transaction_handlers.handle_place_buy_order(stock_code, price, qty, order_dvsn)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.trading.place_stock_order.assert_not_called()
        self.assertIn("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_once()

    async def test_handle_place_buy_order_api_failure(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_api_client.trading.place_stock_order.return_value = {"rt_cd": "1", "msg1": "주문 실패"}

        await self.transaction_handlers.handle_place_buy_order(stock_code, price, qty, order_dvsn)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_api_client.trading.place_stock_order.assert_called_once()
        self.assertIn("주식 매수 주문 실패:", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()

    # 메뉴 4: 실시간 주식 체결가/호가 구독 - handle_realtime_price_quote_stream
    async def test_handle_realtime_price_quote_stream_success(self):
        stock_code = "005930"
        self.mock_api_client.websocket.connect.return_value = True
        self.mock_api_client.websocket.subscribe_realtime_price.return_value = True
        self.mock_api_client.websocket.subscribe_realtime_quote.return_value = True
        self.mock_api_client.websocket.unsubscribe_realtime_price.return_value = True
        self.mock_api_client.websocket.unsubscribe_realtime_quote.return_value = True
        self.mock_api_client.websocket.disconnect.return_value = True

        with mock.patch('asyncio.to_thread', new_callable=mock.AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = [mock.MagicMock(), None]
            await self.transaction_handlers.handle_realtime_price_quote_stream(stock_code)
            mock_to_thread.assert_called_with(builtins.input, "")

        self.mock_api_client.websocket.connect.assert_called_once()
        self.mock_api_client.websocket.subscribe_realtime_price.assert_called_once_with(stock_code)
        self.mock_api_client.websocket.subscribe_realtime_quote.assert_called_once_with(stock_code)
        self.mock_api_client.websocket.unsubscribe_realtime_price.assert_called_once_with(stock_code)
        self.mock_api_client.websocket.unsubscribe_realtime_quote.assert_called_once_with(stock_code)
        self.mock_api_client.websocket.disconnect.assert_called_once()
        self.assertIn(f"--- 실시간 주식 체결가/호가 구독 시작 ({stock_code}) ---", self.print_output_capture.getvalue())
        self.assertIn("실시간 데이터를 수신 중입니다... (종료하려면 Enter를 누르세요)", self.print_output_capture.getvalue())
        self.assertIn("실시간 주식 스트림을 종료했습니다.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called()

    async def test_handle_realtime_price_quote_stream_connection_failure(self):
        stock_code = "005930"
        self.mock_api_client.websocket.connect.return_value = False

        with mock.patch('asyncio.to_thread', new_callable=mock.AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = ""
            await self.transaction_handlers.handle_realtime_price_quote_stream(stock_code)

        self.mock_api_client.websocket.connect.assert_called_once()
        self.mock_api_client.websocket.subscribe_realtime_price.assert_not_called()
        self.assertIn("실시간 웹소켓 연결에 실패했습니다.", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()

    # 메뉴 7: 시가총액 상위 종목 조회 (실전전용) - handle_get_top_market_cap_stocks
    async def test_handle_get_top_market_cap_stocks_success(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {
            "rt_cd": "0", "msg1": "정상", "output": [{"hts_kor_isnm": "삼성전자"}]
        }

        await self.data_handlers.handle_get_top_market_cap_stocks(market_code)

        self.assertIn(f"--- 시가총액 상위 종목 조회 시도 ---", self.print_output_capture.getvalue())
        self.assertIn("성공: 시가총액 상위 종목 목록:", self.print_output_capture.getvalue())
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once_with(market_code)
        self.mock_logger.info.assert_called_once()

    async def test_handle_get_top_market_cap_stocks_paper_trading(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = True

        await self.data_handlers.handle_get_top_market_cap_stocks(market_code)

        self.assertIn("WARNING: 모의투자 환경에서는 시가총액 상위 종목 조회를 지원하지 않습니다.", self.print_output_capture.getvalue())
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_not_called()
        self.mock_logger.warning.assert_called_once()

    async def test_handle_get_top_market_cap_stocks_failure(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = False
        self.mock_api_client.quotations.get_top_market_cap_stocks.return_value = {"rt_cd": "1", "msg1": "오류"}

        await self.data_handlers.handle_get_top_market_cap_stocks(market_code)

        self.assertIn("실패: 시가총액 상위 종목 조회.", self.print_output_capture.getvalue())
        self.mock_api_client.quotations.get_top_market_cap_stocks.assert_called_once()
        self.mock_logger.error.assert_called_once()

    # 메뉴 8: 시가총액 1~10위 종목 현재가 조회 (실전전용) - handle_get_top_10_market_cap_stocks_with_prices
    async def test_handle_get_top_10_market_cap_stocks_with_prices_happy_path(self):
        """
        시장 개장, 실전투자 환경, 모든 API 호출 성공 시 상위 10개 종목 현재가 조회.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        # TradingService의 get_top_market_cap_stocks와 get_current_stock_price를 Mocking
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

        result = await self.data_handlers.handle_get_top_10_market_cap_stocks_with_prices()

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
        self.mock_logger.info.assert_any_call("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")
