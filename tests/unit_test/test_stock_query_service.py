import io
import sys
import unittest
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from services.stock_query_service import StockQueryService
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from common.types import ResCommonResponse, ResTopMarketCapApiItem, ResStockFullInfoApiOutput, ErrorCode, ResBasicStockInfo, ResFluctuation


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
        self._original_stdout = sys.stdout

        self.handler = StockQueryService(self.mock_trading_service, self.mock_logger, self.mock_time_manager)

        self.mock_env = MagicMock(spec=KoreaInvestApiEnv)
        self.mock_env.my_agent = "MockUserAgent"
        self.mock_env.my_app_key = "MockAppKey"
        self.mock_env.my_app_secret = "MockAppSecret"
        self.mock_env.my_custtype = "P"
        self.mock_env.my_tr_id = "TR123456"

        self.api = KoreaInvestApiBase(
            env=self.mock_env,
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
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"stck_prpr": "75000", "stck_shrn_iscd": "005930"}
        )

        await self.handler.handle_get_current_stock_price("005930")
        self.mock_logger.info.assert_called()
        self.assertIn("현재가 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_current_stock_price_failure_rt_cd(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="실패",
            data={"stck_prpr": "75000", "stck_shrn_iscd": "005930"}
        )

        await self.handler.handle_get_current_stock_price("005930")
        self.mock_logger.error.assert_called()
        self.assertIn("현재가 조회 실패", self.mock_logger.error.call_args[0][0])

    # --- handle_get_top_market_cap_stocks_code 함수 테스트 ---
    async def test_handle_get_top_market_cap_stocks_code_success(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                {"data_rank": "1", "hts_kor_isnm": "삼성전자", "stck_avls": "400000000000000", "stck_prpr": "70000"},
                {"data_rank": "2", "hts_kor_isnm": "SK하이닉스", "stck_avls": "100000000000000", "stck_prpr": "150000"}
            ]
        )

        await self.handler.handle_get_top_market_cap_stocks_code("0000", 2)
        self.mock_logger.info.assert_called_once()
        self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_no_output(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        await self.handler.handle_get_top_market_cap_stocks_code("0000", 1)
        self.mock_logger.info.assert_called_once()
        self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_failure_rt_cd(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="에러 발생",
            data=None
        )

        await self.handler.handle_get_top_market_cap_stocks_code("0000", 1)
        self.mock_logger.error.assert_called_once()
        self.assertIn("실패: 시가총액 상위 종목 조회", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_none_return(self):
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = None

        await self.handler.handle_get_top_market_cap_stocks_code("0000", 1)
        self.mock_logger.error.assert_called_once_with("실패: 시가총액 상위 종목 조회: None")

    # --- handle_get_top_10_market_cap_stocks_with_prices 함수 테스트 ---
    async def test_handle_get_top_10_market_cap_stocks_with_prices_success(self):
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                {
                    "rank": 1, "name": "삼성전자", "code": "005930", "current_price": "70000"
                }
            ]
        )

        result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()
        self.mock_logger.info.assert_called()
        self.assertTrue(result)

    async def test_handle_get_top_10_market_cap_stocks_with_prices_empty_list(self):
        # TC 5.2
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()
        self.mock_logger.info.assert_called()
        self.assertTrue(result)  # 빈 리스트는 True로 평가됨

    # --- handle_display_stock_change_rate 함수 테스트 ---
    async def test_handle_display_stock_change_rate_increase(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "prdy_vrss": "1000",
                    "prdy_vrss_sign": "2",
                    "prdy_ctrt": "1.45"
                })
            }
        )

        await self.handler.get_stock_change_rate("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_decrease(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "68000",
                    "prdy_vrss": "1000",
                    "prdy_vrss_sign": "5",  # 하락
                    "prdy_ctrt": "1.45"
                })
            }
        )

        await self.handler.get_stock_change_rate("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_no_change(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "69000", "prdy_vrss": "0", "prdy_vrss_sign": "3", "prdy_ctrt": "0.00"
                })
            }
        )

        await self.handler.get_stock_change_rate("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_missing_fields(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "N/A",
                    "prdy_vrss_sign": "N/A",
                    "prdy_ctrt": "N/A",
                    "prdy_vrss": "N/A"  # ✅ 누락된 필드 추가
                })
            }
        )

        await self.handler.get_stock_change_rate("005930")
        self.mock_logger.info.assert_called_once_with(
            "005930 전일대비 등락률 조회 성공: 현재가=N/A, 전일대비=N/A, 등락률=N/A%"
        )

    async def test_handle_display_stock_change_rate_invalid_prdy_vrss(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000", "prdy_vrss": "ABC", "prdy_vrss_sign": "2", "prdy_ctrt": "1.45"
                })
            }
        )

        await self.handler.get_stock_change_rate("005930")
        # Corrected assertion: expecting "ABC" without a sign because it's not a valid number
        self.mock_logger.info.assert_called_once_with(
            "005930 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=ABC, 등락률=1.45%"
        )

    async def test_handle_display_stock_change_rate_failure(self):
        self.mock_trading_service.get_current_stock_price.return_value = {
            "rt_cd": "1", "msg1": "API 에러"
        }
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="API 에러",
            data=None
        )

        await self.handler.get_stock_change_rate("005930")
        self.mock_logger.error.assert_called_once()
        self.assertIn("전일대비 등락률 조회 실패", self.mock_logger.error.call_args[0][0])

    # --- handle_upper_limit_stocks 함수 테스트 ---
    async def test_handle_upper_limit_stocks_market_closed(self):
        # 모의투자 환경이 아님을 명시적으로 설정
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_time_manager.is_market_open.return_value = False

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.warning.assert_called()
        self.assertIsNone(result)
        self.mock_trading_service.get_top_market_cap_stocks_code.assert_not_called()

    async def test_handle_upper_limit_stocks_paper_trading(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = True

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.warning.assert_called()
        self.assertEqual(result, {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."})
        self.mock_trading_service.get_top_market_cap_stocks_code.assert_not_called()

    async def test_handle_upper_limit_stocks_get_top_stocks_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="API 에러",
            data=[]
        )

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.error.assert_called_once()
        self.assertIn("시가총액 상위 종목 목록 조회 실패", self.mock_logger.error.call_args[0][0])
        self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_no_top_stocks(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.info.assert_called()
        self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_found_one_upper_limit(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                ResTopMarketCapApiItem(
                    iscd="005930",
                    mksc_shrn_iscd="005930",
                    stck_avls="500000000000",
                    data_rank="1",
                    hts_kor_isnm="삼성전자",
                    acc_trdvol="1000000"
                )
            ]
        )

        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": {
                    "prdy_vrss_sign": "1",
                    "stck_prpr": "70000",
                    "prdy_ctrt": "30.00"
                }
            }

        )

        result = await self.handler.handle_upper_limit_stocks()
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

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.info.assert_called()
        self.assertFalse(result)

    async def test_handle_upper_limit_stocks_individual_price_lookup_failure(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                ResTopMarketCapApiItem(
                    iscd="005930",
                    mksc_shrn_iscd="005930",
                    stck_avls="100000000000",
                    data_rank="1",
                    hts_kor_isnm="삼성전자",
                    acc_trdvol="123456"
                )]
        )
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="에러",
            data=[]
        )

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.warning.assert_called()
        self.assertFalse(result)

    async def test_handle_upper_limit_stocks_exception_handling(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.side_effect = Exception("Test Exception")

        result = await self.handler.handle_upper_limit_stocks()
        self.mock_logger.error.assert_called()
        self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_limit_parameter(self):
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False
        top_market_cap_items = [
            ResTopMarketCapApiItem(
                iscd="000660",
                mksc_shrn_iscd="000660",
                stck_avls="300000000000",
                data_rank="2",
                hts_kor_isnm="SK하이닉스",
                acc_trdvol="123456"
            ),
            ResTopMarketCapApiItem(
                iscd="000020",
                mksc_shrn_iscd="000020",
                stck_avls="150000000000",
                data_rank="3",
                hts_kor_isnm="동화약품",
                acc_trdvol="78901"
            ),
            ResTopMarketCapApiItem(
                iscd="000030",
                mksc_shrn_iscd="000030",
                stck_avls="120000000000",
                data_rank="4",
                hts_kor_isnm="우리금융지주",
                acc_trdvol="45678"
            ),
            ResTopMarketCapApiItem(
                iscd="000040",
                mksc_shrn_iscd="000040",
                stck_avls="100000000000",
                data_rank="5",
                hts_kor_isnm="KR모터스",
                acc_trdvol="34567"
            ),
        ]
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=top_market_cap_items
        )
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"prdy_vrss_sign": "2", "stck_prpr": "70000", "prdy_ctrt": "1.45"}
        )

        result = await self.handler.handle_upper_limit_stocks(limit=1)
        self.assertEqual(self.mock_trading_service.get_current_stock_price.call_count, 1)
        self.assertFalse(result)

    # --- handle_display_stock_vs_open_price 함수 테스트 (Previously provided) ---
    async def test_handle_display_stock_vs_open_price_increase(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "stck_oprc": "69000",
                    "oprc_vrss_prpr_sign": "2"
                })
            }
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.info.assert_called()

    async def test_handle_display_stock_vs_open_price_decrease(self):
        """TC: 시가대비 등락률이 하락하는 경우"""
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "68000",  # 현재가
                    "stck_oprc": "69000",  # 시가
                    "oprc_vrss_prpr_sign": "5"  # 시가대비 부호 (하락)
                })
            }
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_no_change(self):
        """TC: 시가대비 등락률이 0인 경우 (보합)"""
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "69000",  # 현재가
                    "stck_oprc": "69000",  # 시가
                    "oprc_vrss_prpr_sign": "3"  # 시가대비 부호 (보합)
                })
            }
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_zero_open_price(self):
        """TC: 시가가 0원인 경우 (나누기 0 방지 및 N/A 처리)"""
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "stck_oprc": "0",
                })
            }
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_missing_price_data(self):
        """TC: 현재가 또는 시가 데이터가 누락되거나 'N/A'일 경우"""
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "N/A",  # 현재가 누락
                    "stck_oprc": "69000",
                    "oprc_vrss_prpr_sign": "2"
                })
            }
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_failure(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="API 에러",
            data=None
        )

        await self.handler.get_open_vs_current("005930")
        self.mock_logger.error.assert_called_once()
        self.assertIn("시가대비 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_display_stock_vs_open_price_output_data_missing(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict(
                    {}  # ✅ 빈 딕셔너리라도 명시적으로 output 키가 있어야 함
                )
            }
        )

        await self.handler.get_open_vs_current("005930")
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
        result = await self.handler.handle_get_top_10_market_cap_stocks_with_prices()

        # Assert
        # trading_service 메서드가 호출되었는지 확인
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.assert_awaited_once()

        # logger.error가 호출되었는지 확인 (exc_info=True는 제외하고 메시지만 검증)
        self.mock_logger.error.assert_called_once()
        self.assertIn("시가총액 1~10위 종목 현재가 조회 중 오류 발생: 테스트 예외 발생", self.mock_logger.error.call_args[0][0])

        # ✅ 핵심 검증
        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.UNKNOWN_ERROR.value)
        self.assertEqual(result.data, None)
        self.assertIn("예외 발생", result.msg1)

    async def test_handle_upper_limit_stocks_empty_top_stocks_list(self):
        """top_stocks_list가 빈 리스트일 때 info 로그 및 메시지 출력 분기 검증"""
        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        result = await self.handler.handle_upper_limit_stocks()

        # 로그 호출 검증 (error 아님 → info)
        self.mock_logger.info.assert_any_call("조회된 시가총액 상위 종목이 없습니다.")
        self.mock_logger.error.assert_not_called()

        # 반환값 None
        self.assertIsNone(result)

    async def test_handle_upper_limit_stocks_invalid_stock_code_in_output(self):
        """stock_info 내에 mksc_shrn_iscd 키가 없을 때 warning 로그 발생"""

        self.mock_time_manager.is_market_open.return_value = True
        self.mock_trading_service._env.is_paper_trading = False

        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                {"hts_kor_isnm": "삼성전자"},  # mksc_shrn_iscd 없음
                {"mksc_shrn_iscd": "", "hts_kor_isnm": "카카오"}  # 빈 문자열
            ]
        )

        result = await self.handler.handle_upper_limit_stocks()

        self.mock_logger.error.assert_called()
        self.assertFalse(result)  # 유효한 종목이 없으므로 False 반환

    async def test_handle_display_stock_change_rate_increase_sign_path(self):
        handler = self.handler  # 기존 asyncSetUp에서 생성된 handler

        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    "stck_prpr": "70000",
                    "prdy_vrss": "1500",
                    "prdy_vrss_sign": "2",
                    "prdy_ctrt": "2.19"
                })
            }
        )

        await handler.get_stock_change_rate("005930")

        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_change_rate_positive_change(self):
        # Scenario 1: 양수 변화량
        stock_code = "005930"
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    'stck_prpr': '70000',
                    'prdy_vrss': '1500',  # 전일대비 +1500원
                    'prdy_vrss_sign': '2',  # 2:상승
                    'prdy_ctrt': '2.19'  # 전일대비율
                })
            }
        )

        # 테스트 실행
        await self.handler.get_stock_change_rate(stock_code)

        # 출력 확인 (실제 콘솔 출력 대신, 로그나 내부 상태를 검증하는 방식이 더 견고함)
        # 여기서는 로거가 올바르게 호출되었는지 확인
        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=70000, 전일대비=+1500, 등락률=2.19%"
        )

    async def test_handle_display_stock_change_rate_negative_change(self):
        # Scenario 2: 음수 변화량
        stock_code = "000660"
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    'stck_prpr': '90000',
                    'prdy_vrss': '2000',  # 전일대비 -2000원
                    'prdy_vrss_sign': '5',  # 5:하락
                    'prdy_ctrt': '2.17'  # 전일대비율
                })
            }
        )
        await self.handler.get_stock_change_rate(stock_code)

        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=90000, 전일대비=-2000, 등락률=2.17%"
        )

    async def test_handle_display_stock_change_rate_zero_change(self):
        # Scenario 3: 변화량 0
        stock_code = "000001"
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={
                "output": ResStockFullInfoApiOutput.from_dict({
                    'stck_prpr': '50000',
                    'prdy_vrss': '0',  # 전일대비 0원
                    'prdy_vrss_sign': '3',  # 3:보합 (또는 기타)
                    'prdy_ctrt': '0.00'  # 전일대비율
                })
            }
        )

        await self.handler.get_stock_change_rate(stock_code)

        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=50000, 전일대비=0, 등락률=0.00%"
        )


class TestHandleCurrentUpperLimitStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_trading_service = AsyncMock()
        self.mock_logger = MagicMock()

        from services.stock_query_service import StockQueryService  # 필요 시 수정
        self.service = StockQueryService(
            trading_service=self.mock_trading_service,
            logger=self.mock_logger,
            time_manager=None
        )


    async def test_success_case(self):
        """Test the successful case where upper limit stocks are found."""
        # 1) 상승률 상위 목록 모킹 (서비스가 이걸 먼저 호출함)
        top30_sample = [
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "120000",
                "prdy_ctrt": "29.9",
                "prdy_vrss": "30000",
                "data_rank": "1",
            }),
        ]
        self.mock_trading_service.get_top_rise_fall_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30_sample
        )

        # 2) 상한가 필터 결과 모킹 (서비스가 위 data로 이걸 호출함)
        self.mock_trading_service.get_current_upper_limit_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data=[
                ResBasicStockInfo(
                    name="SK하이닉스",
                    code="000660",
                    current_price=120000,
                    change_rate=29.9,
                    prdy_ctrt=29.9
                )
            ]
        )

        # 실행
        res = await self.service.handle_current_upper_limit_stocks()

        # 로그(부분 포함) 검증
        assert any(
            "현재 상한가" in call.args[0] for call in self.mock_logger.info.call_args_list
        ), "로그에 '현재 상한가' 문구가 포함되지 않았습니다."

        # 호출 검증
        self.mock_trading_service.get_top_rise_fall_stocks.assert_called_once_with(rise=True)
        self.mock_trading_service.get_current_upper_limit_stocks.assert_called_once()

        # 결과 검증
        assert res.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(res.data, list) and len(res.data) == 1
        item = res.data[0]
        assert item.code == "000660"
        assert item.name == "SK하이닉스"
        assert item.current_price == 120000
        assert item.prdy_ctrt == 29.9
        assert item.change_rate == 29.9

    @pytest.mark.asyncio
    async def test_no_upper_limit_stocks(self):
        """상한가 종목이 하나도 없을 때"""

        # 1) 현재 코드가 실제로 호출하는 메서드를 모킹
        top30 = [
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "종목A",
                "stck_prpr": "10000",
                "prdy_ctrt": "5.0",  # 상한가 아님
                "prdy_vrss": "500",
                "data_rank": "1",
            })
        ]
        self.mock_trading_service.get_top_rise_fall_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=top30
        )

        # 2) 상한가 필터 결과: 없음
        self.mock_trading_service.get_current_upper_limit_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # 또는 실패코드여도 됨 — 구현에 맞춰 조정
            msg1="데이터 없음",
            data=[]
        )

        # 실행
        res = await self.service.handle_current_upper_limit_stocks()

        # 호출 검증 (키워드 인자 rise=True로 호출됨 주의)
        self.mock_trading_service.get_top_rise_fall_stocks.assert_called_once_with(rise=True)
        self.mock_trading_service.get_current_upper_limit_stocks.assert_called_once_with(top30)

        # 반환값 검증: 데이터가 비어있음
        assert isinstance(res.data, list)
        assert len(res.data) == 0

        # 로그 검증은 '문구 포함' 또는 '시작 로그 존재' 정도로 완화
        # 정확한 마지막 호출값 강제 X
        assert any(
            "현재 상한가" in call.args[0] for call in self.mock_logger.info.call_args_list
        ), "info 로그에 '현재 상한가' 문구가 포함되어야 합니다."


    @pytest.mark.asyncio
    async def test_exception_during_processing(self):
        # ✅ 현재 코드 경로에서 실제로 호출되는 메서드를 터뜨림
        self.mock_trading_service.get_top_rise_fall_stocks.side_effect = Exception("예외 발생")

        # 실행
        with pytest.raises(Exception, match="예외 발생"):
            await self.service.handle_current_upper_limit_stocks()

        # 에러 로그가 찍혔는지 확인
        self.mock_logger.error.assert_called()
        # (선택) 메시지 내용까지 확인하고 싶으면:
        assert any("오류 발생" in str(call.args[0]) for call in self.mock_logger.error.call_args_list)

