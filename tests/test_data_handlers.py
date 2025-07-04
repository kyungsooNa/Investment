import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from app.data_handlers import DataHandlers
import sys
import io

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
        self.handler = DataHandlers(self.mock_trading_service, self.mock_logger, self.mock_time_manager)

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

    async def test_handle_display_stock_vs_open_price_output_data_missing(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "0",
            "output": {}
        }
        with patch('builtins.print') as mock_print:
            await self.handler.handle_display_stock_vs_open_price("005930")
            mock_print.assert_called()
            self.mock_logger.info.assert_called()

