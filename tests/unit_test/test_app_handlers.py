import unittest
import unittest.mock as mock
from unittest.mock import AsyncMock
import logging
from io import StringIO
import builtins
from common.types import (
    ResStockFullInfoApiOutput, ResCommonResponse, ErrorCode, ResTopMarketCapApiItem,
)

# 테스트할 모듈 임포트
from services.stock_query_service import StockQueryService
from services.order_execution_service import OrderExecutionService
# from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from services.trading_service import TradingService
# from brokers.korea_investment.korea_invest_quotations_api import KoreaInvestApiQuotations
# from brokers.korea_investment.korea_invest_account_api import KoreaInvestApiAccount
# from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
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


class TestAppHandlers(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        """각 테스트 메서드 실행 전에 필요한 Mock 객체와 핸들러 인스턴스를 설정합니다."""
        # 종속성 목(Mock) 객체 생성
        self.mock_env = mock.Mock(spec=KoreaInvestApiEnv)
        self.mock_env.is_paper_trading = False  # 기본값 설정
        self.mock_logger = MockLogger()
        self.mock_time_manager = mock.AsyncMock(spec_set=TimeManager)
        self.mock_time_manager.is_market_open.return_value = True  # 기본값 설정 (시장이 열려있다고 가정)

        self.mock_broker_api_wrapper = mock.AsyncMock()
        # self.mock_broker_api_wrapper.client = mock.AsyncMock(spec=KoreaInvestApiClient)
        # 하위 API 클라이언트들을 Mock 객체로 할당. 이들 자체에 spec_set을 적용.
        # self.mock_broker_api_wrapper.client.quotations = mock.AsyncMock(spec_set=KoreaInvestApiQuotations)
        # self.mock_broker_api_wrapper.client.account = mock.AsyncMock(spec_set=KoreaInvestApiAccount)
        # self.mock_broker_api_wrapper.client.trading = mock.AsyncMock(spec_set=KoreaInvestApiTrading)

        # TradingService 인스턴스 생성 (주입)
        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager
        )

        # DataHandlers와 TransactionHandlers 인스턴스 생성
        self.stock_query_service = StockQueryService(self.trading_service, self.mock_logger, self.mock_time_manager)
        self.order_execution_service = OrderExecutionService(self.trading_service, self.mock_logger,
                                                             self.mock_time_manager)

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
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"stck_prpr": "70000"}
        )

        await self.stock_query_service.handle_get_current_stock_price(stock_code)

        self.mock_broker_api_wrapper.get_current_price.assert_called_once_with(stock_code)
        assert self.mock_logger.info.called

        self.assertEqual(self.mock_logger.info.call_count, 3)

    async def test_handle_get_current_stock_price_failure(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="Error",
            data=None
        )

        await self.stock_query_service.handle_get_current_stock_price(stock_code)

        self.mock_broker_api_wrapper.get_current_price.assert_called_once_with(stock_code)

        self.mock_logger.info.assert_called()
        self.mock_logger.error.assert_called_once()


    # 메뉴 3: 주식 매수 주문 - handle_place_buy_order
    async def test_handle_place_buy_order_market_open_success(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="주문 성공",
            data=None  # 실제 응답 구조에 따라 변경 가능
        )
        await self.order_execution_service.handle_place_buy_order(stock_code, price, qty)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_broker_api_wrapper.place_stock_order.assert_called_once_with(
            stock_code=stock_code,
            order_price=price,
            order_qty=qty,
            is_buy=True
        )
        self.assertIn("주식 매수 주문 성공:", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([  # <--- 이 부분의 call.count가 2
            mock.call(f"Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"),
            mock.call(f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={{'rt_cd': '0', 'msg1': '주문 성공'}}")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_place_buy_order_market_closed(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = False

        await self.order_execution_service.handle_place_buy_order(stock_code, price, qty)

        self.mock_time_manager.is_market_open.assert_called_once()
        self.mock_broker_api_wrapper.place_stock_order.assert_not_called()
        self.assertIn("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.", self.print_output_capture.getvalue())
        self.mock_logger.warning.assert_called_once()

    async def test_handle_place_buy_order_api_failure(self):
        stock_code = "005930"
        price = "58500"
        qty = "1"
        order_dvsn = "00"

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_broker_api_wrapper.place_stock_order = mock.AsyncMock(return_value=ResCommonResponse(
            rt_cd="1",
            msg1="주문 실패",
            data=None
        ))

        result = await self.order_execution_service.handle_place_buy_order(stock_code, price, qty)

        self.assertEqual(result.rt_cd, "1")
        self.assertIn("주문 실패", result.msg1)
        self.mock_logger.error.assert_any_call("주식 매수 주문 실패: 종목=005930, 결과={'rt_cd': '1', 'msg1': '주문 실패'}")

    # 메뉴 4: 실시간 주식 체결가/호가 구독 - handle_realtime_price_quote_stream
    async def test_handle_realtime_price_quote_stream_success(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.connect_websocket.return_value = True
        self.mock_broker_api_wrapper.subscribe_realtime_price.return_value = True
        self.mock_broker_api_wrapper.subscribe_realtime_quote.return_value = True
        self.mock_broker_api_wrapper.unsubscribe_realtime_price.return_value = True
        self.mock_broker_api_wrapper.unsubscribe_realtime_quote.return_value = True
        self.mock_broker_api_wrapper.disconnect_websocket.return_value = True

        with mock.patch('asyncio.to_thread', new_callable=mock.AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = [mock.MagicMock(), None]
            await self.order_execution_service.handle_realtime_price_quote_stream(stock_code)
            mock_to_thread.assert_called_with(builtins.input)

        self.mock_broker_api_wrapper.connect_websocket.assert_called_once()
        self.mock_broker_api_wrapper.subscribe_realtime_price.assert_called_once_with(stock_code)
        self.mock_broker_api_wrapper.subscribe_realtime_quote.assert_called_once_with(stock_code)
        self.mock_broker_api_wrapper.unsubscribe_realtime_price.assert_called_once_with(stock_code)
        self.mock_broker_api_wrapper.unsubscribe_realtime_quote.assert_called_once_with(stock_code)
        self.mock_broker_api_wrapper.disconnect_websocket.assert_called_once()
        self.assertIn(f"--- 실시간 주식 체결가/호가 구독 시작 ({stock_code}) ---", self.print_output_capture.getvalue())
        self.assertIn("실시간 데이터를 수신 중입니다... (종료하려면 Enter를 누르세요)", self.print_output_capture.getvalue())
        self.assertIn("실시간 주식 스트림을 종료했습니다.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called()

    # 메뉴 5: 주식 전일대비 등락률 조회 - handle_display_stock_change_rate
    async def test_handle_display_stock_change_rate_success(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "prdy_vrss": "500",
                    "prdy_vrss_sign": "2",
                    "prdy_ctrt": "0.72"
                })
            }
        )
        await self.stock_query_service.handle_display_stock_change_rate(stock_code)

        self.assertIn(f"--- {stock_code} 전일대비 등락률 조회 ---", self.print_output_capture.getvalue())
        self.assertIn(f"성공: {stock_code} (70000원)", self.print_output_capture.getvalue())
        self.assertIn("전일대비: +500원", self.print_output_capture.getvalue())
        self.assertIn("전일대비율: 0.72%", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call(f"Trading_Service - {stock_code} 현재가 조회 요청"),
            mock.call(f"{stock_code} 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=+500, 등락률=0.72%")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_display_stock_change_rate_failure(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,  # 예: "100"
            msg1="조회 실패",
            data=None
        )
        await self.stock_query_service.handle_display_stock_change_rate(stock_code)

        self.assertIn(f"실패: {stock_code} 전일대비 등락률 조회.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once_with(f"Trading_Service - {stock_code} 현재가 조회 요청")
        self.mock_logger.error.assert_called_once()

        # 메뉴 6: 주식 시가대비 조회 - handle_display_stock_vs_open_price

    async def test_handle_display_stock_vs_open_price_success(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "stck_oprc": "69000",
                    "oprc_vrss_prpr_sign": "2"
                })
            }
        )
        await self.stock_query_service.handle_display_stock_vs_open_price(stock_code)

        self.assertIn(f"--- {stock_code} 시가대비 조회 ---", self.print_output_capture.getvalue())
        self.assertIn(f"성공: {stock_code}", self.print_output_capture.getvalue())
        self.assertIn("현재가: 70000원", self.print_output_capture.getvalue())
        self.assertIn("시가: 69000원", self.print_output_capture.getvalue())
        self.assertIn("시가대비 등락률: +1000원 (+1.45%)", self.print_output_capture.getvalue())

        self.mock_logger.info.assert_has_calls([
            mock.call(f"Trading_Service - {stock_code} 현재가 조회 요청"),
            mock.call(f"{stock_code} 시가대비 조회 성공: 현재가=70000, 시가=69000, 시가대비=+1000원 (+1.45%)")
        ])
        self.assertEqual(self.mock_logger.info.call_count, 2)

    async def test_handle_display_stock_vs_open_price_failure(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.get_current_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1="조회 실패",
            data=None
        )
        await self.stock_query_service.handle_display_stock_vs_open_price(stock_code)

        self.assertIn(f"실패: {stock_code} 시가대비 조회.", self.print_output_capture.getvalue())
        self.mock_logger.info.assert_called_once_with(f"Trading_Service - {stock_code} 현재가 조회 요청")
        self.mock_logger.error.assert_called_once()

    async def test_handle_realtime_price_quote_stream_connection_failure(self):
        stock_code = "005930"
        self.mock_broker_api_wrapper.connect_websocket.return_value = False

        with mock.patch('asyncio.to_thread', new_callable=mock.AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = ""
            await self.order_execution_service.handle_realtime_price_quote_stream(stock_code)

        self.mock_broker_api_wrapper.connect_websocket.assert_called_once()
        self.mock_broker_api_wrapper.subscribe_realtime_price.assert_not_called()
        self.assertIn("실시간 웹소켓 연결에 실패했습니다.", self.print_output_capture.getvalue())
        self.mock_logger.error.assert_called_once()

    # 메뉴 7: 시가총액 상위 종목 조회 (실전전용) - handle_get_top_market_cap_stocks
    async def test_handle_get_top_market_cap_stocks_success(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = False
        self.mock_broker_api_wrapper.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                ResTopMarketCapApiItem(
                    iscd="005930",
                    mksc_shrn_iscd="005930",
                    hts_kor_isnm="삼성전자",
                    data_rank="1",
                    stck_avls="500조",
                    acc_trdvol="1000000"
                )
            ]
        )

        await self.stock_query_service.handle_get_top_market_cap_stocks_code(market_code, 10)

        self.assertIn(f"--- 시가총액 상위 종목 조회 시도 ---", self.print_output_capture.getvalue())
        self.assertIn("성공: 시가총액 상위 종목 목록:", self.print_output_capture.getvalue())
        self.mock_broker_api_wrapper.get_top_market_cap_stocks_code.assert_called_once_with(market_code, 10)

        self.assertGreaterEqual(self.mock_logger.info.call_count, 1)

    async def test_handle_get_top_market_cap_stocks_paper_trading(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = True

        await self.stock_query_service.handle_get_top_market_cap_stocks_code(market_code, 10)

        self.assertIn("\n--- 시가총액 상위 종목 조회 시도 ---\n실패: 시가총액 상위 종목 조회.\n", self.print_output_capture.getvalue())
        self.mock_broker_api_wrapper.get_top_market_cap_stocks_code.assert_not_called()
        self.mock_logger.warning.assert_called_once()

    async def test_handle_get_top_market_cap_stocks_failure(self):
        market_code = "0000"
        self.mock_env.is_paper_trading = False

        self.mock_broker_api_wrapper.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="오류",
            data=None
        )

        await self.stock_query_service.handle_get_top_market_cap_stocks_code(market_code)

        self.assertIn("실패: 시가총액 상위 종목 조회.", self.print_output_capture.getvalue())
        self.mock_broker_api_wrapper.get_top_market_cap_stocks_code.assert_called_once()
        self.mock_logger.error.assert_called_once()

    # 메뉴 8: 시가총액 1~10위 종목 현재가 조회 (실전전용) - handle_get_top_10_market_cap_stocks_with_prices
    async def test_handle_get_top_10_market_cap_stocks_with_prices_happy_path(self):
        """
        시장 개장, 실전투자 환경, 모든 API 호출 성공 시 상위 10개 종목 현재가 조회.
        """
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False

        # TradingService의 get_top_market_cap_stocks와 get_current_stock_price를 Mocking
        self.trading_service.get_top_market_cap_stocks_code = AsyncMock(return_value=ResCommonResponse(
            rt_cd="0",
            msg1="성공",
            data=[
                ResTopMarketCapApiItem(
                    iscd=f"ISCD{i}",  # ✅ 필수 추가
                    mksc_shrn_iscd=f"CODE{i}",
                    hts_kor_isnm=f"종목명{i}",
                    data_rank=str(i + 1),
                    stck_avls=f"시총{i}",
                    acc_trdvol=str(100000 + i * 1000)  # ✅ 필수 추가
                )
                for i in range(10)
            ]
        ))

        self.trading_service.get_current_stock_price = AsyncMock(side_effect=[
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="정상 처리되었습니다.",
                data={
                    "output": ResStockFullInfoApiOutput.from_dict({
                        "stck_prpr": str(10000 + i * 100)
                    })
                }
            )
            for i in range(10)
        ])

        await self.stock_query_service.handle_get_top_10_market_cap_stocks_with_prices()

        self.mock_time_manager.is_market_open.assert_called_once()
        self.trading_service.get_top_market_cap_stocks_code.assert_called_once_with('0000')
        self.assertEqual(self.trading_service.get_current_stock_price.call_count, 10)

        self.mock_logger.info.assert_any_call("Service - 시가총액 1~10위 종목 현재가 조회 요청")
        self.mock_logger.info.assert_any_call("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")
