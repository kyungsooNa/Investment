import io
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from app.stock_query_service import DataHandlers
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_token_manager import TokenManager

# 모든 테스트를 하나의 AsyncioTestCase 클래스 내에 통합합니다.
# asyncSetUp 메서드가 모든 비동기 테스트 케이스에 대해 올바르게 mock 객체를 초기화하도록 합니다.
# 동기 메서드(_get_sign_from_code)도 이 클래스 내에서 테스트할 수 있습니다.
class TestDataHandlers(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        """
        각 테스트 메서드 실행 전에 필요한 Mock 객체와 DataHandlers 인스턴스를 초기화합니다.
        unittest.IsolatedAsyncioTestCase는 이 asyncSetUp을 각 async/sync 테스트 전에 실행합니다.
        """
        self.mock_trading_service = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_time_manager = MagicMock()
        # _env 속성이 필요한 경우를 위해 AsyncMock으로 설정
        self.mock_trading_service._env = AsyncMock()
        self.print_output_capture = io.StringIO()
        self._original_stdout = sys.stdout
        sys.stdout = self.print_output_capture

        self.handler = DataHandlers(self.mock_trading_service, self.mock_logger, self.mock_time_manager)

        self.mock_token_manager = MagicMock(spec=TokenManager)

        self.api = KoreaInvestApiBase(
            base_url="https://mock.api",
            headers={"Authorization": "Bearer expired"},
            config={
                "base_url": "https://mock.api",
                "tr_ids": {},
                "custtype": "P"
            },
            token_manager=self.mock_token_manager,
            logger=self.mock_logger
        )

    # --- _get_sign_from_code 함수 테스트 (Synchronous) ---
    def test_get_sign_from_code_plus(self):
        self.assertEqual(self.handler._get_sign_from_code('1'), "+")
        self.assertEqual(self.handler._get_sign_from_code('2'), "+")

    def test_get_sign_from_code_minus(self):
        self.assertEqual(self.handler._get_sign_from_code('4'), "-")
        self.assertEqual(self.handler._get_sign_from_code('5'), "-")

    def test_get_sign_from_code_no_sign(self):
        self.assertEqual(self.handler._get_sign_from_code('3'), "")
        self.assertEqual(self.handler._get_sign_from_code('9'), "")
        self.assertEqual(self.handler._get_sign_from_code('ABC'), "")

    # --- handle_get_current_stock_price 함수 테스트 ---
    async def test_handle_get_current_stock_price_success(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0", "output": {"stck_prpr": "75000", "stck_shrn_iscd": "005930"}
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_current_stock_price("005930")
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()
            self.assertIn("현재가 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_current_stock_price_failure_rt_cd(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "1", "msg1": "조회 실패"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_current_stock_price("005930")
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertIn("현재가 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_current_stock_price_failure_none_return(self):
        self.mock_trading_service.get_current_stock_price.return_value = None
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_current_stock_price("005930")
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once_with("005930 현재가 조회 실패: None")

    # --- handle_get_account_balance 함수 테스트 ---
    async def test_handle_get_account_balance_success(self):
        self.mock_trading_service.get_account_balance.return_value = {
            "rt_cd": "0", "output": {"dnca_tot_amt": "1000000"}
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_account_balance()
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()
            self.assertIn("계좌 잔고 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_account_balance_failure_rt_cd(self):
        self.mock_trading_service.get_account_balance.return_value = {
            "rt_cd": "1", "msg1": "에러"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_account_balance()
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertIn("계좌 잔고 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_account_balance_failure_none_return(self):
        self.mock_trading_service.get_account_balance.return_value = None
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_account_balance()
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once_with("계좌 잔고 조회 실패: None")

    # --- handle_get_top_market_cap_stocks_code 함수 테스트 ---
    async def test_handle_get_top_market_cap_stocks_code_success(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": [
                {"data_rank": "1", "hts_kor_isnm": "삼성전자", "stck_avls": "400000000000000", "stck_prpr": "70000"},
                {"data_rank": "2", "hts_kor_isnm": "SK하이닉스", "stck_avls": "100000000000000", "stck_prpr": "150000"}
            ]
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_top_market_cap_stocks("0000", 2)
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()
            self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_no_output(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": []
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_top_market_cap_stocks("0000", 1)
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()
            self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_failure_rt_cd(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "1", "msg1": "에러 발생"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_top_market_cap_stocks("0000", 1)
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertIn("실패: 시가총액 상위 종목 조회", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_none_return(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = None
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_top_market_cap_stocks("0000", 1)
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once_with("실패: 시가총액 상위 종목 조회: None")

    async def test_handle_get_top_market_cap_stocks_missing_output_key(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_get_top_market_cap_stocks("0000", 1)
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()
            self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    # --- handle_get_top_10_market_cap_stocks_with_prices 함수 테스트 ---
    async def test_handle_get_top_10_market_cap_stocks_with_prices_success(self):
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = [
            {"rank": 1, "name": "삼성전자", "code": "005930", "current_price": "70000"}
        ]
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()
            self.mock_logger.info.assert_called()
            self.assertTrue(result)


    async def test_handle_get_top_10_market_cap_stocks_with_prices_empty_list(self):
        # TC 5.2
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = []
        with patch('builtins.print'): # patch 'builtins.print' but don't assert specific calls
            result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()
            self.mock_logger.info.assert_called()
            self.assertTrue(result) # 빈 리스트는 True로 평가됨

    async def test_handle_get_top_10_market_cap_stocks_with_prices_failure_none_return(self):
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = None
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()
            self.mock_logger.info.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertFalse(result)

    # --- handle_display_stock_change_rate 함수 테스트 ---
    async def test_handle_display_stock_change_rate_increase(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000", "prdy_vrss": "1000", "prdy_vrss_sign": "2", "prdy_ctrt": "1.45"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            mock_print.assert_called()
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_decrease(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "68000", "prdy_vrss": "1000", "prdy_vrss_sign": "5", "prdy_ctrt": "1.45"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_no_change(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "69000", "prdy_vrss": "0", "prdy_vrss_sign": "3", "prdy_ctrt": "0.00"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_missing_fields(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "N/A", "prdy_vrss_sign": "N/A", "prdy_ctrt": "N/A"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once_with(
                "005930 전일대비 등락률 조회 성공: 현재가=N/A, 전일대비=N/A, 등락률=N/A%"
            )

    async def test_handle_display_stock_change_rate_invalid_prdy_vrss(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000", "prdy_vrss": "ABC", "prdy_vrss_sign": "2", "prdy_ctrt": "1.45"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            # Corrected assertion: expecting "ABC" without a sign because it's not a valid number
            mock_print.assert_called()
            mock_print.assert_called()
            self.mock_logger.info.assert_called_once_with(
                "005930 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=ABC, 등락률=1.45%"
            )

    async def test_handle_display_stock_change_rate_failure(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "1", "msg1": "API 에러"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_change_rate("005930")
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertIn("전일대비 등락률 조회 실패", self.mock_logger.error.call_args[0][0])

    # --- handle_upper_limit_stocks 함수 테스트 ---
    async def test_handle_upper_limit_stocks_market_closed(self):
        # 모의투자 환경이 아님을 명시적으로 설정
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_time_manager.is_market_open.return_value = False
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.warning.assert_called()
            self.assertIsNone(result)
            self.mock_trading_service.get_top_market_cap_stocks_code.assert_not_called()

    async def test_handle_upper_limit_stocks_paper_trading(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = True
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.warning.assert_called()
            self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})
            self.mock_trading_service.get_top_market_cap_stocks_code.assert_not_called()

    async def test_handle_upper_limit_stocks_get_top_stocks_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "1", "msg1": "API 에러"
        }
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.error.assert_called_once()
            self.assertIn("시가총액 상위 종목 목록 조회 실패", self.mock_logger.error.call_args[0][0])
            self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_no_top_stocks(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": []
        }
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.info.assert_called()
            self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_found_one_upper_limit(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]}
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0", "output": {"prdy_vrss_sign": "1", "stck_prpr": "70000", "prdy_ctrt": "30.00"}}
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.info.assert_called()
            self.assertTrue(result)

    async def test_handle_upper_limit_stocks_no_upper_limit_found(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"}
            ]
        }
        self.mock_trading_service.get_current_stock_price.side_effect = [
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "2", "stck_prpr": "70000", "prdy_ctrt": "1.45"}},
            {"rt_cd": "0", "output": {"prdy_vrss_sign": "5", "stck_prpr": "150000", "prdy_ctrt": "-0.50"}}
        ]
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.info.assert_called()
            self.assertFalse(result)

    async def test_handle_upper_limit_stocks_individual_price_lookup_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": [{"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"}]
        }
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "1", "msg1": "에러"
        }
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.warning.assert_called()
            self.assertFalse(result)

    async def test_handle_upper_limit_stocks_exception_handling(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.side_effect = Exception("Test Exception")

        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()
            mock_print.assert_called()
            self.mock_logger.error.assert_called()
            self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_limit_parameter(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0", "output": [
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"},
                {"mksc_shrn_iscd": "000020", "hts_kor_isnm": "동화약품"},
                {"mksc_shrn_iscd": "000030", "hts_kor_isnm": "우리금융지주"},
                {"mksc_shrn_iscd": "000040", "hts_kor_isnm": "KR모터스"},
            ]
        }
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0", "output": {"prdy_vrss_sign": "2", "stck_prpr": "70000", "prdy_ctrt": "1.45"}
        }

        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks(limit=1)
            self.assertEqual(self.mock_trading_service.get_current_stock_price.call_count, 1)
            mock_print.assert_called()
            mock_print.assert_called()
            self.assertFalse(result)

    # --- handle_display_stock_vs_open_price 함수 테스트 (Previously provided) ---
    async def test_handle_display_stock_vs_open_price_increase(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000",
                "stck_oprc": "69000",
                "oprc_vrss_prpr_sign": "2"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_called()
            self.mock_logger.info.assert_called()

    async def test_handle_display_stock_vs_open_price_decrease(self):
        """TC: 시가대비 등락률이 하락하는 경우"""
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "68000",  # 현재가
                "stck_oprc": "69000",  # 시가
                "oprc_vrss_prpr_sign": "5"  # 시가대비 부호 (하락)
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_any_call("  시가대비 등락률: -1000원 (-1.45%)")  # -1.449... -> -1.45
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_no_change(self):
        """TC: 시가대비 등락률이 0인 경우 (보합)"""
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "69000", # 현재가
                "stck_oprc": "69000", # 시가
                "oprc_vrss_prpr_sign": "3" # 시가대비 부호 (보합)
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_any_call("  시가대비 등락률: 0원 (0.00%)")
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_zero_open_price(self):
        """TC: 시가가 0원인 경우 (나누기 0 방지 및 N/A 처리)"""
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000",
                "stck_oprc": "0", # 시가 0원
                "oprc_vrss_prpr_sign": "2"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_any_call("  시가대비 등락률: +70000원 (N/A)") # 시가대비 금액은 계산되지만, 퍼센트는 N/A
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_missing_price_data(self):
        """TC: 현재가 또는 시가 데이터가 누락되거나 'N/A'일 경우"""
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "N/A",  # 현재가 누락
                "stck_oprc": "69000",
                "oprc_vrss_prpr_sign": "2"
            }
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_any_call("  현재가: N/A원")
            mock_print.assert_any_call("  시가대비 등락률: N/A원 (N/A)")  # 둘 다 N/A로 출력
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_failure(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "1", "msg1": "API 에러"
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_any_call("\n--- 005930 시가대비 조회 ---")
            mock_print.assert_any_call("\n실패: 005930 시가대비 조회.")
            self.mock_logger.error.assert_called_once()
            self.assertIn("시가대비 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_display_stock_vs_open_price_output_data_missing(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {}
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_called()
            self.mock_logger.info.assert_called()

    async def test_handle_get_top_10_market_cap_stocks_with_prices_exception_covered(self):
        """
        TC: 시가총액 1~10위 종목 현재가 조회 중 예외 발생 시,
            except 블록이 실행되고 False를 반환하는지 테스트
        """
        # Arrange
        # trading_service의 해당 메서드가 호출될 때 Exception을 발생시키도록 설정
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.side_effect = Exception("테스트 예외 발생")

        # Act
        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()

            # Assert
            # trading_service 메서드가 호출되었는지 확인
            self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.assert_awaited_once()

            # print 함수가 호출되었는지 확인
            mock_print.assert_any_call("\n--- 시가총액 1~10위 종목 현재가 조회 시도 ---")
            mock_print.assert_any_call("\n실패: 시가총액 1~10위 종목 현재가 조회.")

            # logger.error가 호출되었는지 확인 (exc_info=True는 제외하고 메시지만 검증)
            self.mock_logger.error.assert_called_once()
            self.assertIn("시가총액 1~10위 종목 현재가 조회 중 오류 발생: 테스트 예외 발생", self.mock_logger.error.call_args[0][0])

            # 메서드가 False를 반환하는지 확인
            self.assertFalse(result)

    async def test_handle_upper_limit_stocks_empty_top_stocks_list(self):
        """top_stocks_list가 빈 리스트일 때 info 로그 및 메시지 출력 분기 검증"""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": []  # 빈 리스트 → 236라인 분기
        }

        with patch('builtins.print') as mock_print:
            result = await self.handler.handle_upper_limit_stocks()

            # print 메시지 검증
            mock_print.assert_any_call("조회된 시가총액 상위 종목이 없습니다.\n")

            # 로그 호출 검증 (error 아님 → info)
            self.mock_logger.info.assert_any_call("조회된 시가총액 상위 종목이 없습니다.")
            self.mock_logger.error.assert_not_called()

            # 반환값 None
            self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_invalid_stock_code_in_output(self):
        """stock_info 내에 mksc_shrn_iscd 키가 없을 때 warning 로그 발생"""

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = {
            "rt_cd": "0",
            "output": [
                {"hts_kor_isnm": "삼성전자"},  # mksc_shrn_iscd 없음 → 유효한 종목코드 없음
                {"mksc_shrn_iscd": "", "hts_kor_isnm": "카카오"}  # 빈 문자열도 포함 가능
            ]
        }

        with patch("builtins.print"):
            result = await self.handler.handle_upper_limit_stocks()

            self.mock_logger.warning.assert_any_call(
                "유효한 종목코드를 찾을 수 없습니다: {'hts_kor_isnm': '삼성전자'}"
            )
            self.mock_logger.warning.assert_any_call(
                "유효한 종목코드를 찾을 수 없습니다: {'mksc_shrn_iscd': '', 'hts_kor_isnm': '카카오'}"
            )
            self.assertFalse(result)  # 유효한 종목이 없으므로 False 반환

    async def test_handle_display_stock_change_rate_increase_sign_path(self):
        # 상승 부호 조건 ("2") → "+" 반환
        mock_response = {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000",  # 현재가
                "prdy_vrss": "1500",  # 전일대비
                "prdy_vrss_sign": "2",  # 상승
                "prdy_ctrt": "2.19"  # 등락률
            }
        }

        handler = self.handler  # 기존 asyncSetUp에서 생성된 handler
        self.mock_trading_service.get_current_stock_price.return_value = mock_response

        with patch('builtins.print') as mock_print:
            await handler.handle_display_stock_change_rate("005930")

            # 부호가 붙은 메시지 출력 확인 → 부호 조건 분기 통과
            # 개별적으로 부호와 등락률 출력이 분리되어 있는지 확인
            mock_print.assert_any_call("  전일대비: +1500원")
            mock_print.assert_any_call("  전일대비율: 2.19%")
            self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_positive_change(self):
        # Scenario 1: 양수 변화량
        stock_code = "005930"
        self.mock_trading_service.get_current_stock_price.return_value = {
            'rt_cd': '0',
            'output': {
                'stck_prpr': '70000',
                'prdy_vrss': '1500', # 전일대비 +1500원
                'prdy_vrss_sign': '2', # 2:상승
                'prdy_ctrt': '2.19' # 전일대비율
            }
        }

        # 테스트 실행
        await self.handler.handle_display_stock_change_rate(stock_code)

        # 출력 확인 (실제 콘솔 출력 대신, 로그나 내부 상태를 검증하는 방식이 더 견고함)
        # 여기서는 로거가 올바르게 호출되었는지 확인
        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=+1500, 등락률=2.19%"
        )

    async def test_handle_display_stock_change_rate_negative_change(self):
        # Scenario 2: 음수 변화량
        stock_code = "000660"
        self.mock_trading_service.get_current_stock_price.return_value = {
            'rt_cd': '0',
            'output': {
                'stck_prpr': '90000',
                'prdy_vrss': '2000', # 전일대비 -2000원
                'prdy_vrss_sign': '5', # 5:하락
                'prdy_ctrt': '2.17' # 전일대비율
            }
        }

        await self.handler.handle_display_stock_change_rate(stock_code)

        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=90000, 전일대비=-2000, 등락률=2.17%"
        )

    async def test_handle_display_stock_change_rate_zero_change(self):
        # Scenario 3: 변화량 0
        stock_code = "000001"
        self.mock_trading_service.get_current_stock_price.return_value = {
            'rt_cd': '0',
            'output': {
                'stck_prpr': '50000',
                'prdy_vrss': '0', # 전일대비 0원
                'prdy_vrss_sign': '3', # 3:보합 (또는 기타)
                'prdy_ctrt': '0.00' # 전일대비율
            }
        }

        await self.handler.handle_display_stock_change_rate(stock_code)

        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=50000, 전일대비=0, 등락률=0.00%"
        )
