import pytest
import unittest
import io
from unittest.mock import AsyncMock, MagicMock, patch
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResPriceSummary


class TestGetCurrentUpperLimitStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=None
        )

    async def test_get_current_upper_limit_stocks_success(self):
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            # 상한가 종목
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="정상 처리되었습니다.",
                data=ResPriceSummary(
                    symbol="000660",
                    open=23077,
                    current=30000,
                    change_rate=29.99,
                    prdy_ctrt=29.99
                )
            ),
            # 상한가 아님
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="정상 처리되었습니다.",
                data=ResPriceSummary(
                    symbol="005930",
                    open=79600,
                    current=80000,
                    change_rate=0.5,
                    prdy_ctrt=0.5
                )
            )
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = ["SK하이닉스", "삼성전자"]

        all_codes_input = ["000660", "005930"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 1)

        stock = result.data[0]
        self.assertEqual(stock["code"], "000660")
        self.assertEqual(stock["name"], "SK하이닉스")
        self.assertEqual(stock["current_price"], 30000)
        self.assertAlmostEqual(stock["change_rate"], 29.99)

        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("000660")
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("005930")
        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)

        self.mock_broker_api_wrapper.get_name_by_code.assert_any_call("000660")
        self.mock_broker_api_wrapper.get_name_by_code.assert_any_call("005930")
        self.assertEqual(self.mock_broker_api_wrapper.get_name_by_code.call_count, 2)

        self.mock_logger.info.assert_not_called()

    async def test_get_current_upper_limit_stocks_no_upper_limit(self):
        # 모든 종목이 상한가 조건 불충족
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=MagicMock(current=10000, open=9000, change_rate=5.0, prdy_ctrt=5.0)
            ),
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=MagicMock(current=20000, open=18000, change_rate=7.0, prdy_ctrt=7.0)
            ),
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "종목A", "종목B"
        ]

        all_codes_input = ["000660", "005930"]

        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data, [])  # ✅ 핵심 비교

        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)
        self.assertEqual(self.mock_broker_api_wrapper.get_name_by_code.call_count, 2)
        self.mock_logger.info.assert_not_called()

    async def test_get_current_upper_limit_stocks_api_failure_for_some_stocks(self):
        # CODEF → 예외, CODEC → 정상 (상한가)
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            Exception("API 오류 발생"),
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="정상 처리되었습니다.",
                data=ResPriceSummary(
                    symbol="CODEC",
                    open=30770,
                    current=40000,
                    change_rate=29.99,
                    prdy_ctrt=29.99
                )
            )
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = ["종목C"]

        all_codes_input = ["CODEF", "CODEC"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 1)

        self.assertEqual(result.data[0]["code"], "CODEC")
        self.assertEqual(result.data[0]["name"], "종목C")
        self.assertEqual(result.data[0]["current_price"], 40000)
        self.assertAlmostEqual(result.data[0]["change_rate"], 29.99)

        self.mock_logger.warning.assert_called_once()
        self.assertIn("CODEF 현재 상한가 필터링 중 오류", self.mock_logger.warning.call_args.args[0])

    # ... (나머지 테스트 케이스도 유사하게 get_price_summary와 get_name_by_code 모의를 조정해야 합니다)
    async def test_get_current_upper_limit_stocks_exception_during_iteration(self):
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            ResCommonResponse(  # ✅ 첫 번째 종목 정상 응답
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="정상 처리되었습니다.",
                data=ResPriceSummary(
                    symbol="CODED",
                    open=38460,
                    current=50000,
                    change_rate=29.99,
                    prdy_ctrt=29.99
                )
            ),
            Exception("강제 API 예외")  # ✅ 두 번째 종목 예외
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = ["종목D"]
        all_codes_input = ["CODED", "CODEE"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 1)

        stock = result.data[0]
        self.assertEqual(stock["code"], "CODED")
        self.assertEqual(stock["name"], "종목D")
        self.assertEqual(stock["current_price"], 50000)
        self.assertAlmostEqual(stock["change_rate"], 29.99)

        # 예외 로그 발생 여부 확인
        self.mock_logger.warning.assert_called_with("CODEE 현재 상한가 필터링 중 오류: 강제 API 예외")

    @pytest.mark.asyncio
    async def test_fetch_stock_data_success(self):
        # Arrange
        dummy_code = "123456"
        dummy_price_info = {"current": 10000, "open": 9000, "change_rate": 11.1}
        dummy_name = "테스트종목"

        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(return_value=dummy_price_info)
        self.mock_broker_api_wrapper.get_name_by_code = AsyncMock(return_value=dummy_name)

        # Act
        result = await self.trading_service._fetch_stock_data(dummy_code)

        # Assert
        self.mock_broker_api_wrapper.get_price_summary.assert_called_once_with(dummy_code)
        self.mock_broker_api_wrapper.get_name_by_code.assert_called_once_with(dummy_code)
        self.assertEqual(result, (dummy_price_info, dummy_name))

    @pytest.mark.asyncio
    async def test_get_all_stocks_code_success(self):
        dummy_codes = ["000660", "005930"]
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(return_value=dummy_codes)

        result = await self.trading_service.get_all_stocks_code()

        self.mock_logger.info.assert_called_once_with("Service - 전체 종목 코드 조회 요청")

        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.msg1, "전체 종목 코드 조회 성공")
        self.assertEqual(result.data, dummy_codes)

    @pytest.mark.asyncio
    async def test_get_all_stocks_code_invalid_format(self):
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(return_value={"not": "list"})

        result = await self.trading_service.get_all_stocks_code()

        self.assertEqual(result.rt_cd, ErrorCode.PARSING_ERROR.value)
        self.assertIn("비정상 응답 형식", result.msg1)
        self.mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_stocks_code_exception(self):
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(side_effect=Exception("API 오류"))

        result = await self.trading_service.get_all_stocks_code()

        self.assertEqual(result.rt_cd, ErrorCode.UNKNOWN_ERROR.value)
        self.assertIn("전체 종목 코드 조회 실패", result.msg1)
        self.mock_logger.error.assert_called_once()


class TestGetCurrentUpperLimitStocksFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=None
        )

    @patch('sys.stdout', new_callable=io.StringIO)
    async def test_progress_message_output(self, mock_stdout):
        # ─ Conditions ─
        # 총 종목 수 5개로 설정하여 (idx + 1) == total_stocks 케이스가 명확히 터치되도록 함.
        # progress_step은 max(1, 5 // 10) = 1 이므로, 모든 단계마다 메시지가 나옵니다.
        total_stocks = 5
        all_codes_input = [f"CODE{i:02d}" for i in range(total_stocks)]

        # get_price_summary와 get_name_by_code는 성공적으로 모의합니다.
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            {"current": 10000 + i, "open": 9000 + i, "change_rate": 5.0} for i in range(total_stocks)
        ]
        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            f"종목{i}" for i in range(total_stocks)
        ]

        # ─ Execute ─
        await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        output_value = mock_stdout.getvalue()

        # 예상되는 출력 패턴: "처리 중... N% 완료 (X/Y)"
        expected_messages = []
        progress_step = max(1, total_stocks // 10)

        for i in range(total_stocks):
            # (idx + 1) % progress_step == 0 조건은 모든 단계에서 True
            # (idx + 1) == total_stocks 조건은 마지막 단계에서 True (i=4일 때 5 == 5)
            if (i + 1) % progress_step == 0 or (i + 1) == total_stocks:
                percentage = ((i + 1) / total_stocks) * 100
                expected_messages.append(f"처리 중... {percentage:.0f}% 완료 ({i + 1}/{total_stocks})")

        # 각 예상 메시지가 실제 출력에 포함되어 있는지 확인
        for msg in expected_messages:
            self.assertIn(msg, output_value)

        # 최종적으로 줄을 지우는 메시지가 있는지 확인
        self.assertIn("\r" + " " * 80 + "\r", output_value)

    async def test_get_price_summary_returns_none_skips_stock(self):
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            None,  # CODEF
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=ResPriceSummary(
                    symbol="CODEC",
                    open=30770,
                    current=40000,
                    change_rate=29.99,
                    prdy_ctrt=29.99
                )
            )
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = ["종목C"]

        all_codes_input = ["CODEF", "CODEC"]

        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 1)  # ✅ 이제 통과됨

        item = result.data[0]
        self.assertEqual(item["code"], "CODEC")
        self.assertEqual(item["name"], "종목C")
        self.assertEqual(item["current_price"], 40000)
        self.assertAlmostEqual(item["change_rate"], 29.99)

        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("CODEF")
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("CODEC")
        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)
        self.mock_broker_api_wrapper.get_name_by_code.assert_called_once_with("CODEC")
        self.assertEqual(self.mock_logger.warning.call_count, 1)
        self.assertIn("CODEF 현재 상한가 필터링 중 오류", self.mock_logger.warning.call_args.args[0])


class TestGetYesterdayUpperLimitStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_evn = MagicMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_evn,
            logger=self.mock_logger,
            time_manager=None
        )

    async def test_upper_limit_stock_detected(self):
        # 상한가에 도달한 종목과 아닌 종목을 혼합
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(side_effect=[
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=MagicMock(current=30000, prdy_ctrt=29.9)
            ),
            ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="성공",
                data=MagicMock(current=15000, prdy_ctrt=10.0)
            ),
        ])

        stock_codes = ["000660", "005930"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["code"], "000660")  # 상한가 도달 종목 코드

    async def test_no_upper_limit_stocks(self):
        # 등락률 5%라서 상한가 조건 (29% 이상) 미충족
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(return_value=MagicMock(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data=MagicMock(
                current=15000,
                prdy_ctrt=5.0
            )
        ))

        stock_codes = ["035720"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data, [])  # ✅ 핵심 비교

    async def test_missing_price_info_skipped(self):
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(return_value=None)

        stock_codes = ["068270"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert result.data == []

    @pytest.mark.asyncio
    async def test_exception_during_api_call(self):
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(side_effect=Exception("API 오류"))

        stock_codes = ["001234"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        assert isinstance(result, ResCommonResponse)
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert result.data == []

    def test_handle_realtime_price(self):
        data = {
            "type": "realtime_price",
            "tr_id": "H0STCNT0",
            "data": {
                "MKSC_SHRN_ISCD": "005930",
                "STCK_PRPR": "80000",
                "PRDY_VRSS": "+500",
                "PRDY_VRSS_SIGN": "2",
                "PRDY_CTRT": "0.6",
                "ACML_VOL": "120000",
                "STCK_CNTG_HOUR": "093015"
            }
        }

        self.trading_service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call(
            "실시간 데이터 수신: Type=realtime_price, TR_ID=H0STCNT0, Data={'MKSC_SHRN_ISCD': '005930', 'STCK_PRPR': '80000', "
            "'PRDY_VRSS': '+500', 'PRDY_VRSS_SIGN': '2', 'PRDY_CTRT': '0.6', 'ACML_VOL': '120000', 'STCK_CNTG_HOUR': '093015'}"
        )

    def test_handle_realtime_quote(self):
        data = {
            "type": "realtime_quote",
            "tr_id": "H0STASP0",
            "data": {
                "유가증권단축종목코드": "005930",
                "매도호가1": "80100",
                "매수호가1": "79900",
                "영업시간": "093030"
            }
        }

        self.trading_service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call("실시간 호가 데이터: 005930 매도1=80100, 매수1=79900")

    def test_handle_signing_notice(self):
        data = {
            "type": "signing_notice",
            "tr_id": "H0TR0002",
            "data": {
                "주문번호": "A123456",
                "체결수량": "10",
                "체결단가": "80000",
                "체결시간": "093045"
            }
        }

        self.trading_service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call("체결통보: 주문=A123456, 수량=10, 단가=80000")

    def test_handle_unknown_type(self):
        data = {
            "type": "unknown_type",
            "tr_id": "X0000001",
            "data": {}
        }

        self.trading_service._default_realtime_message_handler(data)
        self.mock_logger.debug.assert_called_once_with(
            "처리되지 않은 실시간 메시지: X0000001 - {'type': 'unknown_type', 'tr_id': 'X0000001', 'data': {}}")

