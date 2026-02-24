import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResFluctuation, ResBasicStockInfo

# --- Pytest Fixtures (새로운 테스트용) ---
@pytest.fixture
def mock_deps():
    broker = MagicMock()
    broker.inquire_daily_itemchartprice = AsyncMock()
    broker.get_current_price = AsyncMock()  # [추가] 현재가 조회 Mock
    
    env = MagicMock()
    
    tm = MagicMock()
    # 기본적으로 현재 시간을 2025-01-02 10:00:00으로 설정 (장 중)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    # to_yyyymmdd는 datetime 객체나 문자열을 받아 YYYYMMDD 문자열 반환
    tm.to_yyyymmdd.side_effect = lambda d: d.strftime("%Y%m%d") if isinstance(d, datetime) else str(d)
    
    return broker, env, tm

@pytest.fixture
def trading_service_fixture(mock_deps):
    broker, env, tm = mock_deps
    service = TradingService(broker, env, time_manager=tm)
    return service

@pytest.mark.asyncio
async def test_fetch_past_daily_ohlcv(trading_service_fixture, mock_deps):
    """과거 데이터 반복 조회 및 병합 테스트"""
    broker, _, _ = mock_deps
    trading_service = trading_service_fixture
    
    # Mock 설정: 2번의 호출에 대해 각각 다른 과거 데이터를 반환
    # 요청은 최신 -> 과거 순으로 진행됨 (종료일 기준 역산)
    # 1. 20250101 (어제) 기준 100일 전 요청 -> 2024-12-01 ~ 2025-01-01 데이터 반환
    # 2. 20241130 기준 100일 전 요청 -> 2024-10-01 ~ 2024-11-30 데이터 반환
    
    # API 응답 데이터 (날짜 오름차순 가정)
    batch1 = [{"stck_bsop_date": "20241201", "stck_clpr": "100"}, {"stck_bsop_date": "20250101", "stck_clpr": "110"}]
    batch2 = [{"stck_bsop_date": "20241001", "stck_clpr": "80"}, {"stck_bsop_date": "20241130", "stck_clpr": "90"}]
    
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="", data=batch1),
        ResCommonResponse(rt_cd="0", msg1="", data=batch2),
        ResCommonResponse(rt_cd="0", msg1="", data=[]) # 3번째는 빈 데이터 (종료)
    ]
    
    # Act
    result = await trading_service._fetch_past_daily_ohlcv("005930", "20250101", max_loops=3)
    
    # Assert
    assert len(result) == 4
    # 날짜순 정렬 확인 (batch2 + batch1)
    assert result[0]['date'] == "20241001"
    assert result[-1]['date'] == "20250101"
    assert broker.inquire_daily_itemchartprice.call_count == 3

@pytest.mark.asyncio
async def test_get_ohlcv_caching(trading_service_fixture, mock_deps):
    """get_ohlcv 메서드의 캐싱 동작 검증"""
    broker, _, tm = mock_deps
    trading_service = trading_service_fixture
    
    # 1. 초기 상태: 캐시 없음
    # 과거 데이터(어제까지) API 호출 + 오늘 데이터 API 호출
    
    # 과거 데이터 Mock (한 번만 호출되도록 설정)
    past_data = [{"stck_bsop_date": "20250101", "stck_clpr": "1000"}]
    
    # [수정] 오늘 데이터 Mock (현재가 조회 결과)
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1020", "stck_lwpr": "990", 
        "stck_prpr": "1010", "acml_vol": "500"
    }
    
    broker.inquire_daily_itemchartprice.side_effect = [
        # _fetch_past_daily_ohlcv 내부 호출 (1회차)
        ResCommonResponse(rt_cd="0", msg1="", data=past_data),
        # _fetch_past_daily_ohlcv 내부 호출 (2회차 - 빈 데이터로 루프 종료)
        ResCommonResponse(rt_cd="0", msg1="", data=[]),
    ]
    
    # [추가] 현재가 조회 Mock 설정
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="", data={"output": today_output}
    )
    
    # Act 1: 첫 번째 호출
    resp1 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp1.rt_cd == "0"
    assert len(resp1.data) == 2 # 과거1 + 오늘1
    assert "005930" in trading_service._daily_ohlcv_cache
    assert trading_service._daily_ohlcv_cache["005930"]["base_date"] == "20250102"
    
    # [수정] API 호출 횟수 확인 (과거조회 2회)
    assert broker.inquire_daily_itemchartprice.call_count == 2
    # [추가] 현재가 조회 1회
    assert broker.get_current_price.call_count == 1
    
    # 2. 두 번째 호출: 캐시 있음 (같은 날짜)
    # 과거 데이터 API 호출은 스킵하고, 오늘 데이터 API만 호출해야 함
    
    broker.inquire_daily_itemchartprice.call_count = 0 # 카운트 리셋
    broker.get_current_price.call_count = 0
    
    # Act 2: 두 번째 호출
    resp2 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp2.rt_cd == "0"
    assert len(resp2.data) == 2
    # [수정] API 호출 횟수 확인 (과거조회 0회, 현재가조회 1회)
    assert broker.inquire_daily_itemchartprice.call_count == 0
    assert broker.get_current_price.call_count == 1

# --- Existing Tests (기존 테스트 복구) ---
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

    async def test_get_current_upper_limit_stocks_parsing_error(self):
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

    async def test_get_all_stocks_code_success(self):
        dummy_codes = ["000660", "005930"]
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(return_value=dummy_codes)

        result = await self.trading_service.get_all_stocks_code()

        self.mock_logger.info.assert_called_once_with("Service - 전체 종목 코드 조회 요청")

        self.assertEqual(result.rt_cd, ErrorCode.SUCCESS.value)
        self.assertEqual(result.msg1, "전체 종목 코드 조회 성공")
        self.assertEqual(result.data, dummy_codes)

    async def test_get_all_stocks_code_invalid_format(self):
        self.mock_broker_api_wrapper.get_all_stock_code_list = AsyncMock(return_value={"not": "list"})

        result = await self.trading_service.get_all_stocks_code()

        self.assertEqual(result.rt_cd, ErrorCode.PARSING_ERROR.value)
        self.assertIn("비정상 응답 형식", result.msg1)
        self.mock_logger.warning.assert_called_once()

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

    def test_handle_realtime_price(self):
        data = {
            "type": "realtime_price",
            "tr_id": "H0STCNT0",
            "data": {
                "유가증권단축종목코드": "005930",
                "주식현재가": "80000",
                "전일대비": "+500",
                "전일대비부호": "2",
                "전일대비율": "0.6",
                "누적거래량": "120000",
                "주식체결시간": "093015"
            }
        }

        self.trading_service._default_realtime_message_handler(data)
        self.mock_logger.info.assert_any_call(
            "실시간 데이터 수신: Type=realtime_price, TR_ID=H0STCNT0, Data={'유가증권단축종목코드': '005930', '주식현재가': '80000', "
            "'전일대비': '+500', '전일대비부호': '2', '전일대비율': '0.6', '누적거래량': '120000', '주식체결시간': '093015'}"
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
