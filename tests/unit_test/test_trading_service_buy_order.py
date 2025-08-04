from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from services.trading_service import TradingService
from common.types import ResCommonResponse, ErrorCode


class TestTradingServiceBuyOrder(IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_time_manager = MagicMock()
        self.mock_env = MagicMock()
        self.mock_broker_api_wrapper = AsyncMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            logger=self.mock_logger,
            env=self.mock_env,
            time_manager=self.mock_time_manager
        )

    async def test_place_buy_order_success(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="주문 성공",
            data=None
        )

        result : ResCommonResponse = await self.trading_service.place_buy_order(
            stock_code="005930",
            price="70000",
            qty="10"
        )

        self.mock_logger.info.assert_any_call(
            "Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 10, 가격: 70000"
        )

        self.mock_broker_api_wrapper.place_stock_order.assert_awaited_once_with(
            stock_code="005930",
            order_price="70000",
            order_qty="10",
            trade_type="buy"
        )

        expected = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="주문 성공",
            data=None
        )

        assert result == expected

    async def test_place_buy_order_failure(self):
        self.mock_broker_api_wrapper.place_stock_order.side_effect = Exception("API 오류 발생")

        result = await self.trading_service.place_buy_order("005930", "70000", "10")

        # 예외를 내부 처리 후 ErrorCode.UNKNOWN_ERROR 반환 확인
        self.assertEqual(result.rt_cd, ErrorCode.UNKNOWN_ERROR.value)
        self.assertIn("예외 발생", result.msg1)

        # 에러 로그 기록 확인
        self.assertTrue(
            any("매수 주문 중 오류 발생" in call_args[0][0] for call_args in self.mock_logger.error.call_args_list)
        )

    async def test_place_buy_order_api_response_failure(self):
        # API는 호출 성공, 그러나 응답 코드 실패 상황 (잔고 부족 등)
        self.mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,  # 예: "1"
            msg1="주문가능금액 부족",
            data=None
        )

        result = await self.trading_service.place_buy_order("005930", "70000", "10")

        # 반환 객체는 ResCommonResponse 인스턴스여야 함
        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert "주문가능금액 부족" in result.msg1

        self.mock_logger.error.assert_any_call("매수 주문 실패: 주문가능금액 부족")


    async def test_place_buy_order_response_missing_msg1(self):
        # ✅ ResCommonResponse가 아닌 dict를 반환해도 예외가 발생하지 않도록 처리할 경우
        self.mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.MISSING_KEY.value,
            msg1="주문 실패",
            data=None
        )

        result = await self.trading_service.place_buy_order("005930", "70000", "10")

        assert result.rt_cd == ErrorCode.MISSING_KEY.value
        assert result.msg1 == "주문 실패"  # 또는 다른 메시지
        self.mock_logger.error.assert_called()

    async def test_place_buy_order_api_failure(self):
        self.mock_broker_api_wrapper.place_stock_order.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value,
            msg1="주문 실패",
            data=None
        )

        result = await self.trading_service.place_buy_order("005930", "70000", "10")

        assert result.rt_cd == ErrorCode.API_ERROR.value
        assert result.msg1 == "주문 실패"  # 또는 다른 메시지
        self.mock_logger.error.assert_called()

    async def test_place_buy_order_exception_logging(self):
        self.mock_broker_api_wrapper.place_stock_order.side_effect = Exception("예상치 못한 오류")

        result = await self.trading_service.place_buy_order("005930", "70000", "10")

        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "예상치 못한 오류" in result.msg1
        self.mock_logger.error.assert_any_call("Service - 매수 주문 중 오류 발생: 예상치 못한 오류")
