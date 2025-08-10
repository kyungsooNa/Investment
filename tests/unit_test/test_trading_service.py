import pytest
import unittest
import io
from unittest.mock import AsyncMock, MagicMock, patch
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResPriceSummary, ResFluctuation, ResBasicStockInfo


class TestGetCurrentUpperLimitStocks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.mock_env = MagicMock()

        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            time_manager=MagicMock()
        )

    async def test_get_current_upper_limit_stocks_success(self):
        rise_stocks = [
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "30000",
                "prdy_ctrt": "29.99",  # 상한가 조건 충족
                "prdy_vrss": "2999",
                "data_rank": "1",
            }),
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "80000",
                "prdy_ctrt": "0.5",  # 상한가 아님
                "prdy_vrss": "400",
                "data_rank": "2",
            }),
        ]

        # ─ Execute ─
        result = await self.trading_service.get_current_upper_limit_stocks(rise_stocks)

        # ─ Assert ─
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, list)
        assert len(result.data) == 1

        only: ResBasicStockInfo = result.data[0]
        assert only.code == "000660"
        assert only.name == "SK하이닉스"
        assert only.current_price == 30000
        assert only.prdy_ctrt == 29.99

    async def test_get_current_upper_limit_stocks_no_upper_limit(self):
        # 모든 종목이 상한가 조건(>29.0) 미충족하도록 구성
        rise_stocks = [
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "종목A",
                "stck_prpr": "10000",
                "prdy_ctrt": "5.0",  # 상한가 아님
                "prdy_vrss": "500",
                "data_rank": "1",
            }),
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "종목B",
                "stck_prpr": "20000",
                "prdy_ctrt": "7.0",  # 상한가 아님
                "prdy_vrss": "1400",
                "data_rank": "2",
            }),
        ]

        # 이 경로에선 요약/이름 조회를 사용하지 않으므로 기존 모킹은 제거해도 됩니다.
        result = await self.trading_service.get_current_upper_limit_stocks(rise_stocks)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, list)
        assert len(result.data) == 0  # 상한가 종목 없음


    @pytest.mark.asyncio
    async def test_get_current_upper_limit_stocks_no_upper_limit(self):
        # 모두 "잘못된 값"이라 파싱 실패 → 스킵되어 상한가 없음
        rise_stocks = [
            # 1) 현재가가 숫자가 아님 → int("N/A")에서 예외
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "종목A",
                "stck_prpr": "N/A",  # ← 고의로 잘못된 값
                "prdy_ctrt": "30.0",  # (의미 없음, 위에서 이미 터짐)
                "prdy_vrss": "0",
                "data_rank": "1",
            }),
            # 2) 등락률이 숫자가 아님 → float("notnum")에서 예외
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "종목B",
                "stck_prpr": "20000",
                "prdy_ctrt": "notnum",  # ← 고의로 잘못된 값
                "prdy_vrss": "1400",
                "data_rank": "2",
            }),
        ]

        # 이 경로에선 요약/이름 조회 호출 안 됨 → 기존 모킹 제거하거나, 남겨뒀다면 아래처럼 검증 가능
        # self.mock_broker_api_wrapper.get_price_summary.assert_not_called()
        # self.mock_broker_api_wrapper.get_name_by_code.assert_not_called()

        result = await self.trading_service.get_current_upper_limit_stocks(rise_stocks)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, list)
        assert len(result.data) == 0  # 모든 항목이 예외로 스킵 → 상한가 없음


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
            time_manager=MagicMock()
        )


    async def test_get_price_summary_returns_none_skips_stock(self):
        rise_stocks = [
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "CODEF",
                "hts_kor_isnm": "종목F",
                "stck_prpr": "30770",
                "prdy_ctrt": "28.0",  # ← 상한가 조건 미충족 → 스킵
                "prdy_vrss": "0",
            }),
            ResFluctuation.from_dict({
                "stck_shrn_iscd": "CODEC",
                "hts_kor_isnm": "종목C",
                "stck_prpr": "40000",
                "prdy_ctrt": "30.0",  # ← 상한가 조건 만족
                "prdy_vrss": "0",
            }),
        ]

        result = await self.trading_service.get_current_upper_limit_stocks(rise_stocks)

        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert isinstance(result.data, list)
        # CODEF는 등락률 28.0 → 스킵, CODEC만 포함
        assert len(result.data) == 1
        only = result.data[0]
        assert isinstance(only, ResBasicStockInfo)
        assert only.code == "CODEC"
        assert only.name == "종목C"
        assert only.current_price == 40000
        assert only.prdy_ctrt == 30.0


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
