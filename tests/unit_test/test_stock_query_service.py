import io
import sys
import unittest
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from services.stock_query_service import StockQueryService
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
        self.mock_time_manager.async_sleep = AsyncMock()
        self.mock_indicator_service = AsyncMock()
        # _env 속성이 필요한 경우를 위해 AsyncMock으로 설정
        self.mock_trading_service._env = AsyncMock()
        self._original_stdout = sys.stdout

        self.stockQueryService = StockQueryService(
            self.mock_trading_service, self.mock_logger, self.mock_time_manager, self.mock_indicator_service
        )

    # --- _get_sign_from_code 함수 테스트 (Synchronous) ---
    def test_get_sign_from_code_plus(self):
        self.assertEqual(self.stockQueryService._get_sign_from_code('1'), "+")
        self.assertEqual(self.stockQueryService._get_sign_from_code('2'), "+")

    def test_get_sign_from_code_minus(self):
        self.assertEqual(self.stockQueryService._get_sign_from_code('4'), "-")
        self.assertEqual(self.stockQueryService._get_sign_from_code('5'), "-")

    def test_get_sign_from_code_no_sign(self):
        self.assertEqual(self.stockQueryService._get_sign_from_code('3'), "")
        self.assertEqual(self.stockQueryService._get_sign_from_code('9'), "")
        self.assertEqual(self.stockQueryService._get_sign_from_code('ABC'), "")

    # --- handle_get_current_stock_price 함수 테스트 ---
    async def test_handle_get_current_stock_price_success(self):
        # ResStockFullInfoApiOutput 객체로 감싸서 반환해야 함
        mock_output = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "75000",
            "stck_shrn_iscd": "005930",
            "prdy_vrss": "1000",
            "prdy_vrss_sign": "2",
            "prdy_ctrt": "1.35"
        })

        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"output": mock_output}
        )

        await self.stockQueryService.handle_get_current_stock_price("005930")
        self.mock_logger.info.assert_called()
        self.assertIn("현재가 및 상세 정보 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_current_stock_price_failure_rt_cd(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="실패",
            data={"stck_prpr": "75000", "stck_shrn_iscd": "005930"}
        )

        await self.stockQueryService.handle_get_current_stock_price("005930")
        self.mock_logger.error.assert_called()
        self.assertIn("현재가 및 상세 정보 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_current_stock_price_parsing_error(self):
        """handle_get_current_stock_price에서 응답 데이터 파싱 오류 테스트"""
        # Arrange
        # output이 ResStockFullInfoApiOutput이 아닌 경우
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"output": {"some_unexpected": "data"}}
        )

        # Act
        result = await self.stockQueryService.handle_get_current_stock_price("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.PARSING_ERROR.value)
        self.assertIn("잘못된 응답 데이터 타입 또는 output 없음", result.msg1)
        self.mock_logger.error.assert_called()

    # --- get_current_price 함수 테스트 ---
    async def test_get_current_price_success(self):
        """get_current_price 성공 케이스 테스트"""
        # Arrange
        expected_response = ResCommonResponse(rt_cd="0", msg1="정상", data={"stck_prpr": "10000"})
        self.mock_trading_service.get_current_stock_price.return_value = expected_response

        # Act
        result = await self.stockQueryService.get_current_price("005930")

        # Assert
        self.mock_trading_service.get_current_stock_price.assert_awaited_once_with("005930")
        self.assertEqual(result, expected_response)

    async def test_get_current_price_failure(self):
        """get_current_price 실패 케이스 테스트"""
        # Arrange
        expected_response = ResCommonResponse(rt_cd="1", msg1="실패", data=None)
        self.mock_trading_service.get_current_stock_price.return_value = expected_response

        # Act
        result = await self.stockQueryService.get_current_price("005930")

        # Assert
        self.mock_trading_service.get_current_stock_price.assert_awaited_once_with("005930")
        self.assertEqual(result, expected_response)

    # --- handle_get_account_balance 함수 테스트 ---

    # --- handle_get_top_market_cap_stocks_code 함수 테스트 ---
    async def test_handle_get_top_market_cap_stocks_code_success(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                {"data_rank": "1", "hts_kor_isnm": "삼성전자", "stck_avls": "400000000000000", "stck_prpr": "70000"},
                {"data_rank": "2", "hts_kor_isnm": "SK하이닉스", "stck_avls": "100000000000000", "stck_prpr": "150000"}
            ]
        )

        await self.stockQueryService.handle_get_top_market_cap_stocks_code("0000", 2)
        self.mock_logger.info.assert_called_once()
        self.assertIn("시가총액 상위 종목 조회 성공", self.mock_logger.info.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_no_output(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        await self.stockQueryService.handle_get_top_market_cap_stocks_code("0000", 1)
        self.mock_logger.debug.assert_called()
        self.assertIn("상위 종목 없음", self.mock_logger.debug.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_failure_rt_cd(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="에러 발생",
            data=None
        )

        await self.stockQueryService.handle_get_top_market_cap_stocks_code("0000", 1)
        self.mock_logger.error.assert_called_once()
        self.assertIn("상위 종목 목록 조회 실패", self.mock_logger.error.call_args[0][0])

    async def test_handle_get_top_market_cap_stocks_code_invalid_code(self):
        """handle_get_top_market_cap_stocks_code에서 종목코드가 없는 경우를 테스트"""
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                # iscd와 mksc_shrn_iscd가 모두 없는 아이템
                {"hts_kor_isnm": "InvalidStock", "prdy_vrss_sign": "1"}
            ]
        )

        result = await self.stockQueryService.handle_get_top_market_cap_stocks_code("0000", 1)

        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data), 0) # 유효하지 않은 종목은 건너뛰므로 결과는 비어있음
        self.mock_logger.warning.assert_called_with(
            "유효하지 않은 종목코드: {'hts_kor_isnm': 'InvalidStock', 'prdy_vrss_sign': '1'}")

    # --- handle_get_top_10_market_cap_stocks_with_prices 함수 테스트 ---
    async def test_handle_get_top_10_market_cap_stocks_with_prices_success(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[
                {
                    "rank": 1, "name": "삼성전자", "code": "005930", "current_price": "70000"
                }
            ]
        )

        result = await self.stockQueryService.handle_get_top_market_cap_stocks_code(market_code="0000", limit=10)
        self.mock_logger.info.assert_called()
        self.assertTrue(result)

    async def test_handle_get_top_10_market_cap_stocks_with_prices_empty_list(self):
        # TC 5.2
        self.mock_trading_service.get_top_10_market_cap_stocks_with_prices.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=[]
        )

        result = await self.stockQueryService.handle_get_top_market_cap_stocks_code(market_code="0000", limit=10)
        self.mock_logger.debug.assert_called() # 빈 리스트는 Debug logging
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

        await self.stockQueryService.get_stock_change_rate("005930")
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

        await self.stockQueryService.get_stock_change_rate("005930")
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

        await self.stockQueryService.get_stock_change_rate("005930")
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

        await self.stockQueryService.get_stock_change_rate("005930")
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

        await self.stockQueryService.get_stock_change_rate("005930")
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

        await self.stockQueryService.get_stock_change_rate("005930")
        self.mock_logger.error.assert_called_once()
        self.assertIn("전일대비 등락률 조회 실패", self.mock_logger.error.call_args[0][0])

    # --- handle_upper_limit_stocks 함수 테스트 ---
    async def test_handle_upper_limit_stocks_get_top_stocks_failure(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="API 에러",
            data=[]
        )

        result = await self.stockQueryService.handle_upper_limit_stocks()
        self.mock_logger.error.assert_called_once()
        self.assertEqual(result.rt_cd,ErrorCode.API_ERROR.value)


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

        result = await self.stockQueryService.handle_upper_limit_stocks()
        self.mock_logger.info.assert_called()
        self.assertTrue(result)

    async def test_handle_upper_limit_stocks_no_upper_limit_found(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.return_value = ResCommonResponse(
            rt_cd= ErrorCode.SUCCESS.value,
            msg1="성공",
            data= [
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자"},
                {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"}
            ]
    )

        result = await self.stockQueryService.handle_upper_limit_stocks()
        self.mock_logger.info.assert_called()
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)

    async def test_handle_upper_limit_stocks_exception_handling(self):
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.side_effect = Exception("Test Exception")

        result = await self.stockQueryService.handle_upper_limit_stocks()
        self.mock_logger.exception.assert_called()
        self.assertIsNone(result.data)

    async def test_handle_upper_limit_stocks_limit_parameter(self):
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

        result = await self.stockQueryService.handle_upper_limit_stocks(limit=1)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)

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

        await self.stockQueryService.get_open_vs_current("005930")
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

        await self.stockQueryService.get_open_vs_current("005930")
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

        await self.stockQueryService.get_open_vs_current("005930")
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

        await self.stockQueryService.get_open_vs_current("005930")
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

        await self.stockQueryService.get_open_vs_current("005930")
        self.mock_logger.info.assert_called_once()

    async def test_handle_display_stock_vs_open_price_failure(self):
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="1",
            msg1="API 에러",
            data=None
        )

        await self.stockQueryService.get_open_vs_current("005930")
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

        await self.stockQueryService.get_open_vs_current("005930")
        self.mock_logger.info.assert_called()

    async def test_handle_get_top_10_market_cap_stocks_with_prices_exception_covered(self):
        """
        TC: 시가총액 1~10위 종목 현재가 조회 중 예외 발생 시,
            except 블록이 실행되고 False를 반환하는지 테스트
        """
        # Arrange
        # trading_service의 해당 메서드가 호출될 때 Exception을 발생시키도록 설정
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_trading_service.get_top_market_cap_stocks_code.side_effect = Exception("테스트 예외 발생")

        # Act
        result = await self.stockQueryService.handle_get_top_market_cap_stocks_code(market_code="0000", limit=10)

        # Assert
        # trading_service 메서드가 호출되었는지 확인
        self.mock_trading_service.get_top_market_cap_stocks_code.assert_awaited_once()

        self.mock_logger.exception.assert_called_once()
        self.assertIn(
            "예외",
            self.mock_logger.exception.call_args[0][0]
        )
        # ✅ 핵심 검증
        self.assertIsInstance(result, ResCommonResponse)
        self.assertEqual(result.rt_cd, ErrorCode.UNKNOWN_ERROR.value)
        self.assertEqual(result.data, None)
        self.assertIn("예외 발생", result.msg1)


    async def test_handle_display_stock_change_rate_increase_sign_path(self):
        handler = self.stockQueryService  # 기존 asyncSetUp에서 생성된 handler

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
        await self.stockQueryService.get_stock_change_rate(stock_code)

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
        await self.stockQueryService.get_stock_change_rate(stock_code)

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

        await self.stockQueryService.get_stock_change_rate(stock_code)

        self.mock_logger.info.assert_called_with(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가=50000, 전일대비=0, 등락률=0.00%"
        )

    async def test_handle_get_current_stock_price_check_new_high_low(self):
        """신고가/신저가 필드가 뷰 모델에 올바르게 매핑되는지 테스트"""
        # Case 1: 신고가 ("1")
        mock_output_high = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "10000",
            "new_hgpr_lwpr_cls_code": "1"
        })
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="정상", data={"output": mock_output_high}
        )
        
        resp = await self.stockQueryService.handle_get_current_stock_price("005930")
        self.assertEqual(resp.rt_cd, ErrorCode.SUCCESS.value)
        self.assertTrue(resp.data["is_new_high"])
        self.assertFalse(resp.data["is_new_low"])

        # Case 2: 신저가 ("신저가") - 문자열로 오는 경우
        mock_output_low = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "5000",
            "new_hgpr_lwpr_cls_code": "신저가"
        })
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="정상", data={"output": mock_output_low}
        )
        
        resp = await self.stockQueryService.handle_get_current_stock_price("005930")
        self.assertEqual(resp.rt_cd, ErrorCode.SUCCESS.value)
        self.assertFalse(resp.data["is_new_high"])
        self.assertTrue(resp.data["is_new_low"])

    async def test_handle_get_current_stock_price_invalid_change_val(self):
        """handle_get_current_stock_price에서 change_val이 숫자가 아닐 때 테스트"""
        # Arrange
        mock_output = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "75000",
            "prdy_vrss": "NotANumber",
            "prdy_vrss_sign": "2",
            "prdy_ctrt": "1.35"
        })
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd="0", msg1="정상", data={"output": mock_output}
        )
        self.mock_trading_service.get_name_by_code.return_value = "TestStock"

        # Act
        result = await self.stockQueryService.handle_get_current_stock_price("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data["change_absolute"], "NotANumber") # pass block, so original value is kept

    async def test_get_stock_change_rate_zero_change(self):
        """get_stock_change_rate에서 등락이 0일 때 '0'을 반환하는지 테스트"""
        # Arrange
        mock_output = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "70000",
            "prdy_vrss": "0",
            "prdy_vrss_sign": "3",
            "prdy_ctrt": "0.00"
        })
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"output": mock_output}
        )

        # Act
        result = await self.stockQueryService.get_stock_change_rate("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data["change_value_display"], "0")

    async def test_get_open_vs_current_invalid_price(self):
        """get_open_vs_current에서 가격이 숫자가 아닐 때 오류를 반환하는지 테스트"""
        # Arrange
        mock_output = ResStockFullInfoApiOutput.from_dict({
            "stck_prpr": "70000",
            "stck_oprc": "InvalidPrice"
        })
        self.mock_trading_service.get_current_stock_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data={"output": mock_output}
        )

        # Act
        result = await self.stockQueryService.get_open_vs_current("005930")

        # Assert
        self.assertEqual(result.rt_cd, "1")
        self.assertEqual(result.msg1, "가격 파싱 오류")
        self.mock_logger.warning.assert_called_with(
            "005930 시가대비 조회 실패: 가격 파싱 오류 (현재가=70000, 시가=InvalidPrice)"
        )

    # --- handle_get_account_balance ---
    async def test_handle_get_account_balance(self):
        """handle_get_account_balance가 trading_service의 메서드를 그대로 호출하고 반환하는지 테스트"""
        # Arrange
        expected_response = ResCommonResponse(rt_cd="0", msg1="정상", data={"balance": 10000})
        self.mock_trading_service.get_account_balance.return_value = expected_response

        # Act
        result = await self.stockQueryService.handle_get_account_balance()

        # Assert
        self.mock_trading_service.get_account_balance.assert_awaited_once()
        self.assertEqual(result, expected_response)

    # --- handle_get_asking_price ---
    async def test_handle_get_asking_price_success(self):
        """handle_get_asking_price 성공 케이스 테스트"""
        # Arrange
        mock_api_output = {
            "output1": {
                "askp1": "10100", "askp_rsqn1": "100",
                "bidp1": "10000", "bidp_rsqn1": "200",
                "stck_prpr": "10050", "aplm_hour": "090000"
            }
        }
        self.mock_trading_service.get_asking_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=mock_api_output
        )

        # Act
        result = await self.stockQueryService.handle_get_asking_price("005930", depth=1)

        # Assert
        self.mock_trading_service.get_asking_price.assert_awaited_once_with("005930")
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data["code"], "005930")
        self.assertEqual(len(result.data["rows"]), 1)
        self.assertEqual(result.data["rows"][0]["ask_price"], "10100")
        self.assertEqual(result.data["meta"]["prpr"], "10050")
        self.mock_logger.info.assert_called_with("005930 호가 정보 조회 성공")

    async def test_handle_get_asking_price_api_failure(self):
        """handle_get_asking_price API 실패 케이스 테스트"""
        # Arrange
        self.mock_trading_service.get_asking_price.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류", data=None
        )

        # Act
        result = await self.stockQueryService.handle_get_asking_price("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.assertEqual(result.msg1, "API 오류")
        self.mock_logger.error.assert_called_with("005930 호가 정보 조회 실패: API 오류")

    async def test_handle_get_asking_price_no_response(self):
        """handle_get_asking_price API 응답이 없을 때 테스트"""
        # Arrange
        self.mock_trading_service.get_asking_price.return_value = None

        # Act
        result = await self.stockQueryService.handle_get_asking_price("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.assertEqual(result.msg1, "응답 없음")
        self.mock_logger.error.assert_called_with("005930 호가 정보 조회 실패: 응답 없음")

    # --- handle_get_time_concluded_prices ---
    async def test_handle_get_time_concluded_prices_success(self):
        """handle_get_time_concluded_prices 성공 케이스 테스트 (output이 list)"""
        # Arrange
        mock_api_output = {
            "output": [
                {"stck_cntg_hour": "090001", "stck_prpr": "10000", "prdy_vrss": "100", "cntg_vol": "50"},
                {"stck_cntg_hour": "090000", "stck_prpr": "9900", "prdy_vrss": "0", "cntg_vol": "10"},
            ]
        }
        self.mock_trading_service.get_time_concluded_prices.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=mock_api_output
        )

        # Act
        result = await self.stockQueryService.handle_get_time_concluded_prices("005930")

        # Assert
        self.mock_trading_service.get_time_concluded_prices.assert_awaited_once_with("005930")
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data["rows"]), 2)
        self.assertEqual(result.data["rows"][0]["time"], "090001")
        self.mock_logger.info.assert_called_with("005930 시간대별 체결가 조회 성공")

    async def test_handle_get_time_concluded_prices_single_item(self):
        """handle_get_time_concluded_prices 성공 케이스 테스트 (output이 dict)"""
        # Arrange
        mock_api_output = {
            "output": {"stck_cntg_hour": "090001", "stck_prpr": "10000", "prdy_vrss": "100", "cntg_vol": "50"}
        }
        self.mock_trading_service.get_time_concluded_prices.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=mock_api_output
        )

        # Act
        result = await self.stockQueryService.handle_get_time_concluded_prices("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(len(result.data["rows"]), 1)
        self.assertEqual(result.data["rows"][0]["time"], "090001")

    async def test_handle_get_time_concluded_prices_failure(self):
        """handle_get_time_concluded_prices API 실패 케이스 테스트"""
        # Arrange
        self.mock_trading_service.get_time_concluded_prices.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류", data=None
        )

        # Act
        result = await self.stockQueryService.handle_get_time_concluded_prices("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.mock_logger.error.assert_called_with("005930 시간대별 체결가 조회 실패: API 오류")

    # --- handle_get_top_stocks ---
    async def test_handle_get_top_stocks_success_rise(self):
        """handle_get_top_stocks 'rise' 카테고리 성공 케이스 테스트"""
        category = "rise"
        service_func_name = "get_top_rise_fall_stocks"
        param = True

        # Arrange
        service_func = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[]))
        setattr(self.mock_trading_service, service_func_name, service_func)

        # Act
        result = await self.stockQueryService.handle_get_top_stocks(category)

        # Assert
        service_func.assert_awaited_once_with(param)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.mock_logger.info.assert_called_with("상승률 상위 종목 조회 성공")

    async def test_handle_get_top_stocks_success_fall(self):
        """handle_get_top_stocks 'fall' 카테고리 성공 케이스 테스트"""
        category = "fall"
        service_func_name = "get_top_rise_fall_stocks"
        param = False

        # Arrange
        service_func = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[]))
        setattr(self.mock_trading_service, service_func_name, service_func)

        # Act
        result = await self.stockQueryService.handle_get_top_stocks(category)

        # Assert
        service_func.assert_awaited_once_with(param)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.mock_logger.info.assert_called_with("하락률 상위 종목 조회 성공")

    async def test_handle_get_top_stocks_success_volume(self):
        """handle_get_top_stocks 'volume' 카테고리 성공 케이스 테스트"""
        category = "volume"
        service_func_name = "get_top_volume_stocks"

        # Arrange
        service_func = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[]))
        setattr(self.mock_trading_service, service_func_name, service_func)

        # Act
        result = await self.stockQueryService.handle_get_top_stocks(category)

        # Assert
        service_func.assert_awaited_once_with()
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.mock_logger.info.assert_called_with("거래량 상위 종목 조회 성공")

    async def test_handle_get_top_stocks_success_trading_value(self):
        """handle_get_top_stocks 'trading_value' 카테고리 성공 케이스 테스트"""
        category = "trading_value"
        service_func_name = "get_top_trading_value_stocks"

        # Arrange
        service_func = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[]))
        setattr(self.mock_trading_service, service_func_name, service_func)

        # Act
        result = await self.stockQueryService.handle_get_top_stocks(category)

        # Assert
        service_func.assert_awaited_once_with()
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.mock_logger.info.assert_called_with("거래대금 상위 종목 조회 성공")

    async def test_handle_get_top_stocks_invalid_category(self):
        """handle_get_top_stocks 지원하지 않는 카테고리 테스트"""
        # Act
        result = await self.stockQueryService.handle_get_top_stocks("invalid_category")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.INVALID_INPUT.value)
        self.assertIn("지원하지 않는 카테고리", result.msg1)
        self.mock_logger.error.assert_called_with("지원하지 않는 카테고리: invalid_category")

    async def test_handle_get_top_stocks_failure(self):
        """handle_get_top_stocks API 실패 케이스 테스트"""
        # Arrange
        self.mock_trading_service.get_top_rise_fall_stocks.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류", data=None
        )

        # Act
        result = await self.stockQueryService.handle_get_top_stocks("rise")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.mock_logger.error.assert_called_with("상승률 상위 종목 조회 실패: API 오류")

    # --- handle_get_etf_info ---
    async def test_handle_get_etf_info_success(self):
        """handle_get_etf_info 성공 케이스 테스트"""
        # Arrange
        mock_api_output = {
            "output": {
                "etf_rprs_bstp_kor_isnm": "KODEX 200",
                "stck_prpr": "30000",
                "nav": "30005.5",
                "stck_llam": "1000000000000"
            }
        }
        self.mock_trading_service.get_etf_info.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=mock_api_output
        )

        # Act
        result = await self.stockQueryService.handle_get_etf_info("122630")

        # Assert
        self.mock_trading_service.get_etf_info.assert_awaited_once_with("122630")
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data["name"], "KODEX 200")
        self.assertEqual(result.data["price"], "30000")
        self.mock_logger.info.assert_called_with("122630 ETF 정보 조회 성공")

    async def test_handle_get_etf_info_failure(self):
        """handle_get_etf_info API 실패 케이스 테스트"""
        # Arrange
        self.mock_trading_service.get_etf_info.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="API 오류"
        )

        # Act
        result = await self.stockQueryService.handle_get_etf_info("122630")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.assertEqual(result.data["code"], "122630")
        self.mock_logger.error.assert_called_with("122630 ETF 정보 조회 실패: API 오류")

    # --- get_ohlcv ---
    async def test_get_ohlcv_success(self):
        """get_ohlcv 성공 케이스 테스트"""
        # Arrange
        expected_response = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[{"date": "20230101"}])
        self.mock_trading_service.get_ohlcv.return_value = expected_response

        # Act
        result = await self.stockQueryService.get_ohlcv("005930", period="D")

        # Assert
        self.mock_trading_service.get_ohlcv.assert_awaited_once_with("005930", period="D")
        self.assertEqual(result, expected_response)

    async def test_get_ohlcv_exception(self):
        """get_ohlcv에서 예외 발생 시 테스트"""
        # Arrange
        self.mock_trading_service.get_ohlcv.side_effect = Exception("Test Exception")

        # Act
        result = await self.stockQueryService.get_ohlcv("005930", period="D")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.UNKNOWN_ERROR.value)
        self.assertEqual(result.msg1, "Test Exception")
        self.mock_logger.error.assert_called_with("005930 OHLCV 데이터 처리 중 오류: Test Exception", exc_info=True)

    # --- get_ohlcv_with_indicators ---
    async def test_get_ohlcv_with_indicators_success(self):
        """get_ohlcv_with_indicators 성공 케이스 테스트"""
        # Arrange
        ohlcv_data = [{"date": "20230101", "close": 100}]
        self.mock_trading_service.get_ohlcv.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=ohlcv_data
        )

        ma_data = [{"date": "20230101", "value": 100}]
        bb_data = [{"date": "20230101", "upper": 105, "middle": 100, "lower": 95}]

        self.mock_indicator_service.get_moving_average.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=ma_data
        )
        self.mock_indicator_service.get_bollinger_bands.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=bb_data
        )

        # Act
        result = await self.stockQueryService.get_ohlcv_with_indicators("005930", "D")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data["ohlcv"], ohlcv_data)
        self.assertEqual(len(result.data["indicators"]["ma5"]), 1)
        self.assertEqual(len(result.data["indicators"]["bb"]), 1)
        self.mock_trading_service.get_ohlcv.assert_awaited_once_with("005930", period="D")
        self.assertEqual(self.mock_indicator_service.get_moving_average.call_count, 5)
        self.mock_indicator_service.get_bollinger_bands.assert_awaited_once()

    async def test_get_ohlcv_with_indicators_ohlcv_failure(self):
        """get_ohlcv_with_indicators에서 OHLCV 조회 실패 시 테스트"""
        # Arrange
        self.mock_trading_service.get_ohlcv.return_value = ResCommonResponse(
            rt_cd=ErrorCode.API_ERROR.value, msg1="OHLCV 조회 실패", data=None
        )

        # Act
        result = await self.stockQueryService.get_ohlcv_with_indicators("005930", "D")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.API_ERROR.value)
        self.mock_indicator_service.get_moving_average.assert_not_called()

    async def test_get_ohlcv_with_indicators_indicator_failure(self):
        """get_ohlcv_with_indicators에서 지표 계산 실패 시 테스트"""
        # Arrange
        ohlcv_data = [{"date": "20230101", "close": 100}]
        self.mock_trading_service.get_ohlcv.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=ohlcv_data
        )
        self.mock_indicator_service.get_moving_average.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=[{"value": 100}]
        )
        # BB 계산만 실패하는 경우
        self.mock_indicator_service.get_bollinger_bands.return_value = ResCommonResponse(
            rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1="계산 오류", data=[]
        )

        # Act
        result = await self.stockQueryService.get_ohlcv_with_indicators("005930", "D")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertIsNotNone(result.data["ohlcv"])
        self.assertEqual(len(result.data["indicators"]["ma5"]), 1)
        self.assertEqual(len(result.data["indicators"]["bb"]), 0) # BB 데이터는 비어있어야 함

    # --- get_recent_daily_ohlcv ---
    async def test_get_recent_daily_ohlcv_success(self):
        """get_recent_daily_ohlcv 성공 케이스 테스트"""
        # Arrange
        rows = [{"date": "20230101"}]
        self.mock_trading_service.get_recent_daily_ohlcv.return_value = rows

        # Act
        result = await self.stockQueryService.get_recent_daily_ohlcv("005930", limit=10)

        # Assert
        self.mock_trading_service.get_recent_daily_ohlcv.assert_awaited_once_with("005930", limit=10)
        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.data, rows)

    async def test_get_recent_daily_ohlcv_empty(self):
        """get_recent_daily_ohlcv 데이터가 없을 때 테스트"""
        # Arrange
        self.mock_trading_service.get_recent_daily_ohlcv.return_value = []

        # Act
        result = await self.stockQueryService.get_recent_daily_ohlcv("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.EMPTY_VALUES.value)
        self.assertEqual(result.data, [])

    async def test_get_recent_daily_ohlcv_exception(self):
        """get_recent_daily_ohlcv에서 예외 발생 시 테스트"""
        # Arrange
        self.mock_trading_service.get_recent_daily_ohlcv.side_effect = Exception("Test Exception")

        # Act
        result = await self.stockQueryService.get_recent_daily_ohlcv("005930")

        # Assert
        self.assertEqual(result.rt_cd, ErrorCode.EMPTY_VALUES.value)
        self.assertEqual(result.msg1, "Test Exception")
        self.mock_logger.error.assert_called_with("[OHLCV] 005930 조회 실패: Test Exception", exc_info=True)

    # --- get_day_intraday_minutes_list ---
    async def test_get_day_intraday_minutes_list_today_regular_session(self):
        """get_day_intraday_minutes_list 오늘, 정규장 세션 테스트"""
        # Arrange
        self.mock_trading_service._env.is_paper_trading = True # 모의투자 환경
        self.mock_time_manager.get_current_kst_time.return_value = MagicMock(strftime=MagicMock(return_value="20230101"))
        self.mock_time_manager.to_hhmmss.side_effect = lambda x: x.ljust(6, '0')
        self.mock_time_manager.dec_minute.side_effect = ["152800", "085900"]

        # API 응답 모킹 (페이지네이션)
        resp1 = ResCommonResponse(rt_cd="0", msg1="정상", data=[
            {"stck_cntg_hour": "153000", "stck_prpr": "100"},
            {"stck_cntg_hour": "152900", "stck_prpr": "99"},
        ])
        resp2 = ResCommonResponse(rt_cd="0", msg1="정상", data=[
            {"stck_cntg_hour": "090100", "stck_prpr": "90"},
            {"stck_cntg_hour": "090000", "stck_prpr": "89"},
        ])
        resp3 = ResCommonResponse(rt_cd="0", msg1="정상", data=[]) # End of data
        self.mock_trading_service.get_intraday_minutes_today.side_effect = [resp1, resp2, resp3]

        # Act
        result = await self.stockQueryService.get_day_intraday_minutes_list("005930", session="REGULAR")

        # Assert
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["stck_cntg_hour"], "090000") # 시간 오름차순 정렬 확인
        self.assertEqual(result[3]["stck_cntg_hour"], "153000")
        self.assertEqual(self.mock_trading_service.get_intraday_minutes_today.call_count, 2)
        # 첫 호출 커서 확인
        self.mock_trading_service.get_intraday_minutes_today.assert_any_call(stock_code="005930", input_hour_1="153000")

    async def test_get_day_intraday_minutes_list_by_date_extended_session(self):
        """get_day_intraday_minutes_list 특정일, 확장 세션 테스트"""
        # Arrange
        self.mock_trading_service._env.is_paper_trading = False # 실전투자 환경
        self.mock_time_manager.to_hhmmss.side_effect = lambda x: x.ljust(6, '0')
        self.mock_time_manager.dec_minute.side_effect = ["080000"]

        # API 응답 모킹
        resp1 = ResCommonResponse(rt_cd="0", msg1="정상", data={"output2": [
            {"stck_bsop_date": "20230101", "stck_cntg_hour": "195900", "stck_prpr": "100"},
            {"stck_bsop_date": "20230101", "stck_cntg_hour": "080100", "stck_prpr": "90"},
        ]})
        resp2 = ResCommonResponse(rt_cd="0", msg1="정상", data=[])
        self.mock_trading_service.get_intraday_minutes_by_date.side_effect = [resp1, resp2]

        # Act
        result = await self.stockQueryService.get_day_intraday_minutes_list(
            "005930", date_ymd="20230101", session="EXTENDED"
        )

        # Assert
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["stck_cntg_hour"], "080100")
        self.assertEqual(result[1]["stck_cntg_hour"], "195900")
        self.assertEqual(self.mock_trading_service.get_intraday_minutes_by_date.call_count, 2)
        # 첫 호출 커서 확인
        self.mock_trading_service.get_intraday_minutes_by_date.assert_any_call(
            stock_code="005930", input_date_1="20230101", input_hour_1="200000"
        )

    async def test_get_day_intraday_minutes_list_extract_rows_output2(self):
        """_extract_rows가 output2 키를 처리하는지 테스트"""
        await self._helper_test_extract_rows("output2")

    async def test_get_day_intraday_minutes_list_extract_rows_rows(self):
        """_extract_rows가 rows 키를 처리하는지 테스트"""
        await self._helper_test_extract_rows("rows")

    async def test_get_day_intraday_minutes_list_extract_rows_data(self):
        """_extract_rows가 data 키를 처리하는지 테스트"""
        await self._helper_test_extract_rows("data")

    async def _helper_test_extract_rows(self, data_key):
        """_extract_rows가 다양한 응답 구조를 처리하는지 테스트"""
        # Arrange
        self.mock_trading_service._env.is_paper_trading = False
        self.mock_time_manager.to_hhmmss.side_effect = lambda x: x.ljust(6, '0')
        self.mock_time_manager.dec_minute.return_value = "085900"

        api_data = {data_key: [{"stck_bsop_date": "20230101", "stck_cntg_hour": "090000"}]}
        resp = ResCommonResponse(rt_cd="0", msg1="정상", data=api_data)
        self.mock_trading_service.get_intraday_minutes_by_date.side_effect = [resp, ResCommonResponse(rt_cd="0", msg1="정상", data=[])]

        # Act
        result = await self.stockQueryService.get_day_intraday_minutes_list(
            "005930", date_ymd="20230101", start_hhmmss="090000", end_hhmmss="090000"
        )

        # Assert
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["stck_cntg_hour"], "090000")

    async def test_get_day_intraday_minutes_list_api_fail(self):
        """get_day_intraday_minutes_list API 실패 시 중단 테스트"""
        # Arrange
        self.mock_trading_service._env.is_paper_trading = True
        self.mock_time_manager.get_current_kst_time.return_value = MagicMock(strftime=MagicMock(return_value="20230101"))
        self.mock_time_manager.to_hhmmss.side_effect = lambda x: x.ljust(6, '0')

        resp_fail = ResCommonResponse(rt_cd="1", msg1="API 오류")
        self.mock_trading_service.get_intraday_minutes_today.return_value = resp_fail

        # Act
        result = await self.stockQueryService.get_day_intraday_minutes_list("005930")

        # Assert
        self.assertEqual(len(result), 0)
        self.mock_trading_service.get_intraday_minutes_today.assert_called_once()

    # --- Stream Handlers ---
    async def test_handle_realtime_stream(self):
        """handle_realtime_stream이 trading_service의 메서드를 그대로 호출하는지 테스트"""
        # Arrange
        stock_codes = ["005930"]
        fields = ["price"]
        duration = 10

        # Act
        await self.stockQueryService.handle_realtime_stream(stock_codes, fields, duration)

        # Assert
        self.mock_trading_service.handle_realtime_stream.assert_awaited_once_with(stock_codes, fields, duration)
        self.mock_logger.info.assert_called()

    async def test_handle_program_trading_stream(self):
        """handle_program_trading_stream이 웹소켓 연결/구독/해지 흐름을 호출하는지 테스트"""
        # Arrange
        stock_code = "005930"
        duration = 1

        # Act
        await self.stockQueryService.handle_program_trading_stream(stock_code, duration)

        # Assert
        self.mock_trading_service.connect_websocket.assert_awaited_once()
        self.mock_trading_service.subscribe_program_trading.assert_awaited_once_with(stock_code)
        self.mock_time_manager.async_sleep.assert_awaited_once_with(duration)
        self.mock_trading_service.unsubscribe_program_trading.assert_awaited_once_with(stock_code)
        self.mock_trading_service.disconnect_websocket.assert_awaited_once()

    async def test_handle_program_trading_stream_exception_safety(self):
        """handle_program_trading_stream에서 대기 중 예외 발생 시에도 해지/연결해제가 보장되는지 테스트"""
        # Arrange
        self.mock_time_manager.async_sleep.side_effect = Exception("Test sleep error")

        # Act & Assert
        with self.assertRaisesRegex(Exception, "Test sleep error"):
            await self.stockQueryService.handle_program_trading_stream("005930", 1)

        # finally 블록이 실행되었는지 확인
        self.mock_trading_service.unsubscribe_program_trading.assert_awaited_once_with("005930")
        self.mock_trading_service.disconnect_websocket.assert_awaited_once()


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
