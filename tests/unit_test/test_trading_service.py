import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResFluctuation, ResBasicStockInfo

# --- Pytest Fixtures (새로운 테스트용) ---
@pytest.fixture
def mock_deps():
    broker = MagicMock()
    broker.inquire_daily_itemchartprice = AsyncMock()
    broker.get_current_price = AsyncMock()

    env = MagicMock()

    tm = MagicMock()
    # 기본적으로 현재 시간을 2025-01-02 10:00:00으로 설정 (장 중)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    # to_yyyymmdd는 datetime 객체나 문자열을 받아 YYYYMMDD 문자열 반환
    tm.to_yyyymmdd.side_effect = lambda d: d.strftime("%Y%m%d") if isinstance(d, datetime) else str(d)

    logger = MagicMock()

    return broker, env, tm, logger

@pytest.fixture
def trading_service_fixture(mock_deps):
    broker, env, tm, logger = mock_deps
    service = TradingService(broker, env, time_manager=tm, logger=logger)
    return service

@pytest.mark.asyncio
async def test_fetch_past_daily_ohlcv(trading_service_fixture, mock_deps):
    """과거 데이터 반복 조회 및 병합 테스트"""
    broker, _, _, _ = mock_deps
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
    broker, _, tm, _ = mock_deps
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

@pytest.mark.asyncio
async def test_get_current_stock_price(trading_service_fixture, mock_deps):
    """현재가 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={})
    
    resp = await trading_service_fixture.get_current_stock_price("005930")
    assert resp.rt_cd == "0"
    broker.get_current_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_ohlcv_range(trading_service_fixture, mock_deps):
    """기간별 OHLCV 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    
    resp = await trading_service_fixture.get_ohlcv_range("005930", start_date="20250101", end_date="20250110")
    assert resp.rt_cd == "0"
    broker.inquire_daily_itemchartprice.assert_awaited_with(stock_code="005930", start_date="20250101", end_date="20250110", fid_period_div_code="D")

@pytest.mark.asyncio
async def test_get_asking_price(trading_service_fixture, mock_deps):
    """호가 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.get_asking_price = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_asking_price("005930")
    assert resp.rt_cd == "0"
    broker.get_asking_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_time_concluded_prices(trading_service_fixture, mock_deps):
    """시간대별 체결가 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.get_time_concluded_prices = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_time_concluded_prices("005930")
    assert resp.rt_cd == "0"
    broker.get_time_concluded_prices.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_etf_info(trading_service_fixture, mock_deps):
    """ETF 정보 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.get_etf_info = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_etf_info("005930")
    assert resp.rt_cd == "0"
    broker.get_etf_info.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_websocket_connection_methods(trading_service_fixture, mock_deps):
    """웹소켓 연결/해제 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.connect_websocket = AsyncMock(return_value=True)
    broker.disconnect_websocket = AsyncMock(return_value=True)
    
    cb = MagicMock()
    assert await trading_service_fixture.connect_websocket(cb) is True
    broker.connect_websocket.assert_awaited_once_with(on_message_callback=cb)
    
    assert await trading_service_fixture.disconnect_websocket() is True
    broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_websocket_subscription_methods(trading_service_fixture, mock_deps):
    """웹소켓 구독/해제 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.subscribe_realtime_price = AsyncMock(return_value=True)
    broker.unsubscribe_realtime_price = AsyncMock(return_value=True)
    broker.subscribe_realtime_quote = AsyncMock(return_value=True)
    broker.unsubscribe_realtime_quote = AsyncMock(return_value=True)
    
    code = "005930"
    
    assert await trading_service_fixture.subscribe_realtime_price(code) is True
    broker.subscribe_realtime_price.assert_awaited_once_with(code)
    
    assert await trading_service_fixture.unsubscribe_realtime_price(code) is True
    broker.unsubscribe_realtime_price.assert_awaited_once_with(code)
    
    assert await trading_service_fixture.subscribe_realtime_quote(code) is True
    broker.subscribe_realtime_quote.assert_awaited_once_with(code)
    
    assert await trading_service_fixture.unsubscribe_realtime_quote(code) is True
    broker.unsubscribe_realtime_quote.assert_awaited_once_with(code)

@pytest.mark.asyncio
async def test_handle_realtime_stream(trading_service_fixture, mock_deps):
    """실시간 스트림 처리 로직 테스트"""
    broker, _, tm, _ = mock_deps
    broker.connect_websocket = AsyncMock(return_value=True)
    broker.subscribe_realtime_price = AsyncMock(return_value=True)
    broker.unsubscribe_realtime_price = AsyncMock(return_value=True)
    broker.disconnect_websocket = AsyncMock(return_value=True)
    tm.async_sleep = AsyncMock()

    # datetime.now()를 모킹하여 루프 제어
    # 1. start_time 설정
    # 2. while 조건 확인 (진입)
    # 3. while 조건 확인 (탈출)
    base_time = datetime(2025, 1, 1, 12, 0, 0)
    
    with patch("services.trading_service.datetime") as mock_dt:
        mock_dt.now.side_effect = [
            base_time,
            base_time,
            base_time + timedelta(seconds=1.1)
        ]
        
        # price 타입 구독
        await trading_service_fixture.handle_realtime_stream(["005930"], ["price"], 1)

    broker.connect_websocket.assert_awaited_once()
    broker.subscribe_realtime_price.assert_awaited_once_with("005930")
    tm.async_sleep.assert_awaited_once_with(1)
    broker.unsubscribe_realtime_price.assert_awaited_once_with("005930")
    broker.disconnect_websocket.assert_awaited_once()

@pytest.mark.asyncio
async def test_default_realtime_message_handler(trading_service_fixture, mock_deps):
    """실시간 메시지 핸들러 테스트"""
    _, _, _, _ = mock_deps
    service = trading_service_fixture
    
    # 1. realtime_price
    data_price = {
        'type': 'realtime_price',
        'data': {
            '유가증권단축종목코드': '005930',
            '주식현재가': '70000',
            '전일대비': '100',
            '전일대비부호': '2',
            '전일대비율': '0.14',
            '누적거래량': '1000',
            '주식체결시간': '100000'
        }
    }
    service._default_realtime_message_handler(data_price)
    # Verify internal state update
    assert '005930' in service._latest_prices
    assert service._latest_prices['005930']['price'] == '70000'

    # 2. realtime_quote
    data_quote = {
        'type': 'realtime_quote',
        'data': {
            '유가증권단축종목코드': '005930',
            '매도호가1': '70100',
            '매수호가1': '70000',
            '영업시간': '100000'
        }
    }
    service._default_realtime_message_handler(data_quote)
    
    # 3. signing_notice
    data_notice = {
        'type': 'signing_notice',
        'data': {
            '주문번호': '123',
            '체결수량': '10',
            '체결단가': '70000',
            '주식체결시간': '100000'
        }
    }
    service._default_realtime_message_handler(data_notice)

    # 4. realtime_program_trading
    data_pgm = {
        'type': 'realtime_program_trading',
        'data': {
            '주식체결시간': '100000',
            '순매수거래대금': '1000000'
        }
    }
    service._default_realtime_message_handler(data_pgm)

    # 5. Unknown
    data_unknown = {'type': 'unknown', 'tr_id': 'UNK', 'data': {}}
    service._default_realtime_message_handler(data_unknown)

def test_normalize_ohlcv_rows(trading_service_fixture):
    """OHLCV 데이터 정규화 로직 테스트"""
    service = trading_service_fixture
    
    # Case 1: Dict input
    items_dict = [
        {"stck_bsop_date": "20250101", "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90", "stck_clpr": "105", "acml_vol": "1000"},
        {"date": "20250102", "open": "105", "high": "115", "low": "95", "close": "110", "volume": "1200"} # Alternative keys
    ]
    rows = service._normalize_ohlcv_rows(items_dict)
    assert len(rows) == 2
    assert rows[0]['date'] == "20250101"
    assert rows[0]['close'] == 105.0
    assert rows[1]['date'] == "20250102"
    assert rows[1]['volume'] == 1200

    # Case 2: Object input (mock)
    item_obj = MagicMock()
    item_obj.stck_bsop_date = "20250103"
    item_obj.stck_oprc = "110"
    item_obj.stck_hgpr = "120"
    item_obj.stck_lwpr = "100"
    item_obj.stck_clpr = "115"
    item_obj.acml_vol = "1500"
    
    rows = service._normalize_ohlcv_rows([item_obj])
    assert len(rows) == 1
    assert rows[0]['date'] == "20250103"
    assert rows[0]['close'] == 115.0

    # Case 3: Invalid/Missing data
    items_invalid = [
        {}, # Missing date
        {"stck_bsop_date": "20250104", "stck_clpr": "invalid"} # Invalid number
    ]
    rows = service._normalize_ohlcv_rows(items_invalid)
    assert len(rows) == 1
    assert rows[0]['date'] == "20250104"
    assert rows[0]['close'] is None

def test_calc_range_by_period(trading_service_fixture, mock_deps):
    """기간별 날짜 계산 로직 테스트"""
    service = trading_service_fixture
    _, _, tm, _ = mock_deps
    
    # Mock current time
    base_dt = datetime(2025, 6, 1)
    tm.get_current_kst_time.return_value = base_dt
    tm.to_yyyymmdd.side_effect = lambda d: d.strftime("%Y%m%d")
    
    # 1. Daily (D)
    s, e = service._calc_range_by_period("D", None)
    assert e == "20250601"
    # 365 * 2 = 730 days
    expected_start = (base_dt - timedelta(days=730)).strftime("%Y%m%d")
    assert s == expected_start

    # 2. Weekly (W)
    s, e = service._calc_range_by_period("W", base_dt)
    # 52 * 2 = 104 weeks
    expected_start = (base_dt - timedelta(weeks=104)).strftime("%Y%m%d")
    assert s == expected_start

    # 3. Monthly (M)
    s, e = service._calc_range_by_period("M", base_dt)
    # 24 * 2 = 48 -> max(48, 60) = 60 months -> 60 * 31 days
    expected_start = (base_dt - timedelta(days=60*31)).strftime("%Y%m%d")
    assert s == expected_start
    
    # 4. Unknown -> Daily fallback
    s, e = service._calc_range_by_period("X", base_dt)
    # max(120, 240) = 240 days
    expected_start = (base_dt - timedelta(days=240)).strftime("%Y%m%d")
    assert s == expected_start

@pytest.mark.asyncio
async def test_get_ohlcv_non_daily(trading_service_fixture, mock_deps):
    """일봉이 아닌(주봉/월봉) OHLCV 조회 테스트"""
    broker, _, _, _ = mock_deps
    service = trading_service_fixture
    
    # Mock API response
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data=[{"stck_bsop_date": "20250101", "stck_clpr": "1000"}]
    )
    
    resp = await service.get_ohlcv("005930", period="W")
    
    assert resp.rt_cd == "0"
    assert len(resp.data) == 1
    broker.inquire_daily_itemchartprice.assert_awaited()
    # Verify period passed is 'W'
    kwargs = broker.inquire_daily_itemchartprice.call_args.kwargs
    assert kwargs['fid_period_div_code'] == "W"

@pytest.mark.asyncio
async def test_get_all_stocks_code_error(trading_service_fixture, mock_deps):
    """전체 종목 코드 조회 실패 테스트"""
    broker, _, _, _ = mock_deps
    service = trading_service_fixture
    
    broker.get_all_stock_code_list.side_effect = Exception("API Error")
    
    resp = await service.get_all_stocks_code()
    
    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "전체 종목 코드 조회 실패" in resp.msg1

@pytest.mark.asyncio
async def test_get_all_stocks_code_invalid_type(trading_service_fixture, mock_deps):
    """전체 종목 코드 조회 반환 타입 오류 테스트"""
    broker, _, _, _ = mock_deps
    service = trading_service_fixture
    
    broker.get_all_stock_code_list = AsyncMock(return_value="Not a list")
    
    resp = await service.get_all_stocks_code()
    
    assert resp.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "비정상 응답 형식" in resp.msg1

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_with_start_date(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: start_date 지정 시 단일 호출 테스트"""
    broker, _, tm, _ = mock_deps
    service = trading_service_fixture
    
    expected_data = [{"stck_bsop_date": "20250101", "stck_clpr": "100", "stck_oprc": "100", "stck_hgpr": "100", "stck_lwpr": "100", "acml_vol": "100"}]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=expected_data
    )
    
    result = await service.get_recent_daily_ohlcv("005930", start_date="20250101")
    
    assert len(result) == 1
    assert result[0]['date'] == "20250101"
    broker.inquire_daily_itemchartprice.assert_awaited_once()
    kwargs = broker.inquire_daily_itemchartprice.call_args.kwargs
    assert kwargs['start_date'] == "20250101"

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_loop_merging(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: 반복 호출 및 병합 로직 테스트"""
    broker, _, tm, _ = mock_deps
    service = trading_service_fixture
    
    batch1 = [{"stck_bsop_date": "20250105", "stck_clpr": "105"}]
    batch2 = [{"stck_bsop_date": "20250103", "stck_clpr": "103"}, {"stck_bsop_date": "20250104", "stck_clpr": "104"}]
    batch3 = [{"stck_bsop_date": "20250101", "stck_clpr": "101"}, {"stck_bsop_date": "20250102", "stck_clpr": "102"}]
    
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch1),
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch2),
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch3),
    ]
    
    result = await service.get_recent_daily_ohlcv("005930", limit=5)
    
    assert len(result) == 5
    assert result[0]['date'] == "20250101"
    assert result[-1]['date'] == "20250105"
    assert broker.inquire_daily_itemchartprice.call_count == 3

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_api_error_break(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: API 에러 시 중단 테스트"""
    broker, _, _, _ = mock_deps
    service = trading_service_fixture
    
    batch1 = [{"stck_bsop_date": "20250105", "stck_clpr": "105"}]
    
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch1),
        ResCommonResponse(rt_cd="1", msg1="Error", data=[]),
    ]
    
    result = await service.get_recent_daily_ohlcv("005930", limit=5)
    
    assert len(result) == 1
    assert result[0]['date'] == "20250105"

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_overlap_handling(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: 중복 데이터 처리 테스트"""
    broker, _, _, _ = mock_deps
    service = trading_service_fixture
    
    batch1 = [{"stck_bsop_date": "20250104", "stck_clpr": "104"}, {"stck_bsop_date": "20250105", "stck_clpr": "105"}]
    batch2 = [{"stck_bsop_date": "20250103", "stck_clpr": "103"}, {"stck_bsop_date": "20250104", "stck_clpr": "104"}]
    
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch1),
        ResCommonResponse(rt_cd="0", msg1="OK", data=batch2),
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
    ]
    
    result = await service.get_recent_daily_ohlcv("005930", limit=10)
    
    assert len(result) == 3
    assert result[0]['date'] == "20250103"
    assert result[1]['date'] == "20250104"
    assert result[2]['date'] == "20250105"

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_with_end_date(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: end_date 지정 테스트"""
    broker, _, tm, _ = mock_deps
    service = trading_service_fixture
    
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"stck_bsop_date": "20241231", "stck_clpr": "100"}]
    )
    
    await service.get_recent_daily_ohlcv("005930", limit=1, end_date="20241231")
    
    broker.inquire_daily_itemchartprice.assert_awaited()
    kwargs = broker.inquire_daily_itemchartprice.call_args.kwargs
    assert kwargs['end_date'] == "20241231"

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_success(trading_service_fixture, mock_deps):
    """프로그램 매매 구독 해지 성공 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.unsubscribe_program_trading = AsyncMock(return_value=True)
    
    result = await trading_service_fixture.unsubscribe_program_trading("005930")
    
    assert result is True
    broker.unsubscribe_program_trading.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_invalid_code(trading_service_fixture, mock_deps):
    """프로그램 매매 구독 해지 시 종목 코드 누락 테스트"""
    broker, _, _, logger = mock_deps
    broker.unsubscribe_program_trading = AsyncMock()
    
    result = await trading_service_fixture.unsubscribe_program_trading("")
    
    assert result is False
    broker.unsubscribe_program_trading.assert_not_awaited()
    logger.warning.assert_called_with("프로그램 매매 구독 해지를 위한 종목 코드가 누락되었습니다.")

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_exception(trading_service_fixture, mock_deps):
    """프로그램 매매 구독 해지 중 예외 발생 테스트"""
    broker, _, _, logger = mock_deps
    broker.unsubscribe_program_trading = AsyncMock(side_effect=Exception("Network Error"))
    
    result = await trading_service_fixture.unsubscribe_program_trading("005930")
    
    assert result is False
    broker.unsubscribe_program_trading.assert_awaited_once_with("005930")
    logger.error.assert_called_with("프로그램 매매 구독 해지 중 오류 발생: Network Error")

@pytest.mark.asyncio
async def test_get_price_summary(trading_service_fixture, mock_deps):
    """가격 요약 정보 조회 위임 테스트"""
    broker, _, _, logger = mock_deps
    broker.get_price_summary = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_price_summary("005930")
    
    assert resp.rt_cd == "0"
    broker.get_price_summary.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 종목 요약 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_info_by_code(trading_service_fixture, mock_deps):
    """종목 상세 정보 조회 위임 테스트"""
    broker, _, _, logger = mock_deps
    broker.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_info_by_code("005930")
    
    assert resp.rt_cd == "0"
    broker.get_stock_info_by_code.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 종목 상세 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_account_balance(trading_service_fixture, mock_deps):
    """계좌 잔고 조회 위임 테스트"""
    broker, _, _, _ = mock_deps
    broker.get_account_balance = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_account_balance()
    
    assert resp.rt_cd == "0"
    broker.get_account_balance.assert_awaited_once()

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
