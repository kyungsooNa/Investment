import pytest
import unittest
import io
from unittest.mock import AsyncMock, MagicMock, patch
from services.trading_service import TradingService


class TestDefaultRealtimeMessageHandler(unittest.TestCase):
    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_broker_api_wrapper = MagicMock()
        self.mock_env = MagicMock()
        self.service = TradingService(broker_api_wrapper=self.mock_broker_api_wrapper, env=self.mock_env, logger=self.mock_logger)

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

        self.service._default_realtime_message_handler(data)
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

        self.service._default_realtime_message_handler(data)
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

        self.service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call("체결통보: 주문=A123456, 수량=10, 단가=80000")

    def test_handle_unknown_type(self):
        data = {
            "type": "unknown_type",
            "tr_id": "X0000001",
            "data": {}
        }

        self.service._default_realtime_message_handler(data)
        self.mock_logger.debug.assert_called_once_with("처리되지 않은 실시간 메시지: X0000001 - {'type': 'unknown_type', 'tr_id': 'X0000001', 'data': {}}")

@pytest.mark.asyncio
class TestGetTop10MarketCapStocksWithPrices:
    def setup_method(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_env = MagicMock()
        self.mock_time_manager = MagicMock()
        self.mock_logger = MagicMock()
        from services.trading_service import TradingService
        self.service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=self.mock_time_manager,
        )

    async def test_market_closed_returns_none(self):
        self.mock_time_manager.is_market_open.return_value = False
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.warning.assert_any_call("시장이 닫혀 있어 시가총액 1~10위 종목 현재가 조회를 수행할 수 없습니다.")

    async def test_paper_trading_returns_error(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = True
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}

    async def test_get_top_stocks_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "1", "msg1": "API 오류"})
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.error.assert_called()

    async def test_get_top_stocks_empty_list(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": []})
        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result is None
        self.mock_logger.info.assert_any_call("시가총액 상위 종목 목록을 찾을 수 없습니다.")

    async def test_successful_flow_returns_results(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        top_stocks = [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"},
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": top_stocks})
        self.service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "0", "output": {"stck_prpr": "80000"}},
            {"rt_cd": "0", "output": {"stck_prpr": "130000"}},
        ])

        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == [
            {"rank": "1", "name": "삼성전자", "code": "005930", "current_price": "80000"},
            {"rank": "2", "name": "SK하이닉스", "code": "000660", "current_price": "130000"},
        ]
        assert self.service.get_current_stock_price.await_count == 2

    async def test_partial_price_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_env.is_paper_trading = False
        top_stocks = [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "data_rank": "2"},
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(return_value={"rt_cd": "0", "output": top_stocks})
        self.service.get_current_stock_price = AsyncMock(side_effect=[
            {"rt_cd": "1", "msg1": "실패"},
            {"rt_cd": "0", "output": {"stck_prpr": "130000"}},
        ])

        result = await self.service.get_top_10_market_cap_stocks_with_prices()
        assert result == [
            {"rank": "2", "name": "SK하이닉스", "code": "000660", "current_price": "130000"}
        ]

    @pytest.mark.asyncio
    async def test_market_cap_limit_10_enforced(self):
        # ─ Conditions ─
        self.service._time_manager.is_market_open.return_value = True
        self.service._env.is_paper_trading = False

        # 11개 종목 제공
        top_stocks = [
            {"mksc_shrn_iscd": f"00000{i}", "hts_kor_isnm": f"종목{i}", "data_rank": str(i + 1)}
            for i in range(11)
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(
            return_value={"rt_cd": "0", "output": top_stocks}
        )
        self.service.get_current_stock_price = AsyncMock(
            side_effect=[
                {"rt_cd": "0", "output": {"stck_prpr": str(10000 + i)}} for i in range(11)
            ]
        )

        # ─ Execute ─
        result = await self.service.get_top_10_market_cap_stocks_with_prices()

        # ─ Assert ─
        assert len(result) == 10
        assert all("current_price" in r for r in result)
        self.service.get_current_stock_price.assert_awaited()
        assert self.service.get_current_stock_price.await_count == 10  # 11개 중 10개만 처리되어야 함

    @pytest.mark.asyncio
    async def test_top10_result_none_logs_warning(self):

        self.service._time_manager.is_market_open.return_value = True
        self.service._env.is_paper_trading = False

        # 정상 종목 1개지만 현재가 조회 실패
        mock_top_stocks = [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "data_rank": "1"},
        ]
        self.service.get_top_market_cap_stocks_code = AsyncMock(
            return_value={"rt_cd": "0", "output": mock_top_stocks}
        )
        self.service.get_current_stock_price = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "조회 실패"}  # 실패 응답
        )

        # Act
        result = await self.service.get_top_10_market_cap_stocks_with_prices()

        # Assert
        assert result is None
        self.service._logger.warning.assert_any_call("시가총액 1~10위 종목 현재가 조회 결과 없음.")

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
        # 현재가 = 상한가
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(side_effect=[
            {"stck_prpr": "30000", "stck_uppr": "30000", "rate": "29.9"},
            {"stck_prpr": "15000", "stck_uppr": "20000", "rate": "10.0"}
        ])

        stock_codes = ["000660", "005930"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "000660")
        self.assertEqual(result[0]["price"], 30000)
        self.assertAlmostEqual(result[0]["change_rate"], 29.9)

    async def test_no_upper_limit_stocks(self):
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(return_value={
            "stck_prpr": "15000", "stck_uppr": "30000", "rate": "5.0"
        })

        stock_codes = ["035720"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)
        self.assertEqual(result, [])

    async def test_missing_price_info_skipped(self):
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(return_value=None)

        stock_codes = ["068270"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)
        self.assertEqual(result, [])

    async def test_exception_during_api_call(self):
        self.mock_broker_api_wrapper.get_price_summary = AsyncMock(side_effect=Exception("API 오류"))

        stock_codes = ["001234"]
        result = await self.trading_service.get_yesterday_upper_limit_stocks(stock_codes)

        self.assertEqual(result, [])
        self.mock_logger.warning.assert_called_once()
        self.assertIn("001234 상한가 필터링 중 오류", self.mock_logger.warning.call_args.args[0])

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
        # ─ Conditions ─
        # trading_service.py의 get_current_upper_limit_stocks가 기대하는 형식에 맞춰
        # get_price_summary의 반환 값 설정 (current, open, change_rate 키 사용)
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            # 000660 (상한가 조건 충족)
            {"symbol": "000660", "open": 23077, "current": 30000, "change_rate": 29.99},
            # 005930 (상한가 조건 미충족)
            {"symbol": "005930", "open": 79600, "current": 80000, "change_rate": 0.50}
        ]

        # get_name_by_code 모의 (get_current_upper_limit_stocks 내부에서 호출됨)
        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "SK하이닉스", # 000660에 대한 응답
            "삼성전자"    # 005930에 대한 응답
        ]

        # get_current_upper_limit_stocks에 전달될 입력 데이터 구조
        all_codes_input = ["000660", "005930"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "000660")
        self.assertEqual(result[0]["name"], "SK하이닉스")
        self.assertEqual(result[0]["current_price"], 30000)
        self.assertAlmostEqual(result[0]["change_rate"], 29.99)

        # mock_broker_api_wrapper.get_price_summary가 각 종목에 대해 호출되었는지 확인
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("000660")
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("005930")
        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)

        # mock_broker_api_wrapper.get_name_by_code가 각 종목에 대해 호출되었는지 확인
        self.mock_broker_api_wrapper.get_name_by_code.assert_any_call("000660")
        self.mock_broker_api_wrapper.get_name_by_code.assert_any_call("005930")
        self.assertEqual(self.mock_broker_api_wrapper.get_name_by_code.call_count, 2)

        self.mock_logger.info.assert_not_called() # 상한가 종목이 있을 때는 경고 로그가 없어야 함

    # ... (나머지 테스트 케이스는 위에서 수정한 get_price_summary의 반환값에 맞춰 필요시 수정)
    # 예시로 test_get_current_upper_limit_stocks_no_upper_limit을 수정해 드립니다.
    async def test_get_current_upper_limit_stocks_no_upper_limit(self):
        # ─ Conditions ─
        # 모든 종목이 상한가가 아님
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            {"symbol": "CODEA", "open": 9000, "current": 10000, "change_rate": 5.0},
            {"symbol": "CODEB", "open": 18000, "current": 20000, "change_rate": 7.0}
        ]
        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "종목A",
            "종목B"
        ]
        all_codes_input = ["000660", "005930"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertEqual(result, [])
        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)
        self.assertEqual(self.mock_broker_api_wrapper.get_name_by_code.call_count, 2)
        self.mock_logger.info.assert_not_called()

    async def test_get_current_upper_limit_stocks_api_failure_for_some_stocks(self):
        # ─ Conditions ─
        # 첫 번째 종목 CODEF → 예외 발생
        # 두 번째 종목 CODEC → 정상, 상한가

        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            Exception("API 오류 발생"),  # CODEF는 예외 발생
            {"current": 40000, "open": 30770, "change_rate": 29.99}  # CODEC은 정상
        ]

        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "종목C"  # CODEC에 대한 이름만 필요
        ]

        all_codes_input = ["CODEF", "CODEC"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "CODEC")
        self.assertEqual(result[0]["name"], "종목C")
        self.assertEqual(result[0]["current_price"], 40000)
        self.assertAlmostEqual(result[0]["change_rate"], 29.99)

        self.mock_logger.warning.assert_called_once()
        self.assertIn("CODEF 현재 상한가 필터링 중 오류", self.mock_logger.warning.call_args.args[0])

    # ... (나머지 테스트 케이스도 유사하게 get_price_summary와 get_name_by_code 모의를 조정해야 합니다)
    async def test_get_current_upper_limit_stocks_exception_during_iteration(self):
        # ─ Conditions ─
        # 두 번째 종목 조회 중 예외 발생
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            {"current": 50000, "open": 38460, "change_rate": 29.99}, # CODED에 대한 성공 응답
            Exception("강제 API 예외") # CODEE 처리 중 예외 발생
        ]
        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "종목D", # CODED에 대한 이름
            "종목E"  # CODEE에 대한 이름 (이 호출은 예외 때문에 발생하지 않을 수도 있음)
        ]
        all_codes_input = ["CODED", "CODEE"] # 입력 변경

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "CODED")
        self.mock_logger.warning.assert_called_once() # 예외 발생 시 warning 로깅
        self.assertIn("CODEE 현재 상한가 필터링 중 오류", self.mock_logger.warning.call_args.args[0])
        # get_name_by_code는 첫 번째 종목에 대해 호출되고, 두 번째 종목 get_price_summary에서 예외 발생
        # 따라서 get_name_by_code는 한 번만 호출될 것입니다.
        self.assertEqual(self.mock_broker_api_wrapper.get_name_by_code.call_count, 1)

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
        self.assertEqual(result, {"rt_cd": "0", "output": dummy_codes})

    @pytest.mark.asyncio
    async def test_get_all_stocks_code_invalid_format(self):
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(return_value={"not": "list"})

        result = await self.trading_service.get_all_stocks_code()

        self.assertEqual(result["rt_cd"], "1")
        self.assertIn("비정상 응답 형식", result["msg1"])
        self.mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_stocks_code_exception(self):
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(side_effect=Exception("API 오류"))

        result = await self.trading_service.get_all_stocks_code()

        self.assertEqual(result["rt_cd"], "1")
        self.assertIn("전체 종목 코드 조회 실패", result["msg1"])
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
        # ─ Conditions ─
        # 첫 번째 종목에 대해 get_price_summary가 None을 반환하도록 모의
        # 두 번째 종목은 정상적으로 상한가 조건 만족
        self.mock_broker_api_wrapper.get_price_summary.side_effect = [
            None,  # 첫 번째 종목 (CODEF)에 대해 None 반환
            {"current": 40000, "open": 30770, "change_rate": 29.99}  # 두 번째 종목 (CODEC) 성공
        ]
        # get_name_by_code는 실제로 호출될 종목명만 순서대로 넣어줍니다.
        # CODEF는 get_price_summary가 None을 반환하여 건너뛰므로, '종목F'는 호출되지 않습니다.
        self.mock_broker_api_wrapper.get_name_by_code.side_effect = [
            "종목C"  # CODEC에 대한 이름만 필요
        ]
        all_codes_input = ["CODEF", "CODEC"]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(all_codes_input)

        # ─ Assert ─
        self.assertEqual(len(result), 1)  # CODEF는 스킵되고 CODEC만 결과에 포함되어야 함
        self.assertEqual(result[0]["code"], "CODEC")
        self.assertEqual(result[0]["name"], "종목C")  # 이제 '종목C'가 올바르게 검증됨
        self.assertEqual(result[0]["current_price"], 40000)
        self.assertAlmostEqual(result[0]["change_rate"], 29.99)

        # get_price_summary가 두 번 호출되었는지 확인
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("CODEF")
        self.mock_broker_api_wrapper.get_price_summary.assert_any_call("CODEC")
        self.assertEqual(self.mock_broker_api_wrapper.get_price_summary.call_count, 2)

        # get_name_by_code는 CODEC에 대해서만 호출되었는지 확인
        self.mock_broker_api_wrapper.get_name_by_code.assert_called_once_with("CODEC")

        # `if not price_info:` 조건에서 continue 될 때 경고 로그는 발생하지 않으므로, 호출되지 않아야 함
        self.mock_logger.warning.assert_not_called()
