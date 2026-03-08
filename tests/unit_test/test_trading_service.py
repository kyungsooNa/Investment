import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from services.trading_service import TradingService
from common.types import ErrorCode, ResCommonResponse, ResFluctuation, ResBasicStockInfo
from types import SimpleNamespace

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
    cache_manager = MagicMock()  # Added cache_manager mock

    return SimpleNamespace(
        broker=broker,
        env=env,
        tm=tm,
        logger=logger,
        cache_manager=cache_manager
    )

@pytest.fixture
def trading_service_fixture(mock_deps):
    # broker, env, tm, logger, cache_manager = mock_deps
    # TradingService currently doesn't accept cache_manager in __init__ based on provided context,
    # but we pass what it accepts.
    service = TradingService(
        broker_api_wrapper=mock_deps.broker,
        env=mock_deps.env,
        time_manager=mock_deps.tm,
        logger=mock_deps.logger,
        cache_manager=mock_deps.cache_manager
    )
    return service

@pytest.mark.asyncio
async def test_fetch_past_daily_ohlcv(trading_service_fixture, mock_deps):
    """과거 데이터 반복 조회 및 병합 테스트"""
    broker = mock_deps.broker
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
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
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
    
    # 캐시 미스 설정
    cache_manager.get_raw.return_value = None

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
    
    # 캐시 저장 호출 확인
    cache_manager.set.assert_called()
    assert "ohlcv_past_005930" in cache_manager.set.call_args[0][0]
    
    # [수정] API 호출 횟수 확인 (과거조회 2회)
    assert broker.inquire_daily_itemchartprice.call_count == 2
    # [추가] 현재가 조회 1회
    assert broker.get_current_price.call_count == 1
    
    # 2. 두 번째 호출: 캐시 있음 (같은 날짜)
    # 과거 데이터 API 호출은 스킵하고, 오늘 데이터 API만 호출해야 함
    
    broker.inquire_daily_itemchartprice.call_count = 0 # 카운트 리셋
    broker.get_current_price.call_count = 0
    
    # 캐시 히트 설정 (오늘 날짜 20250102)
    # [수정] OHLCV 비교 로직을 위해 필수 키(open, high, low, close, volume) 포함
    cached_item = {"date": "20250101", "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100}
    cached_data = {'base_date': '20250102', 'data': [cached_item]}
    cache_manager.get_raw.return_value = (
        {'data': cached_data, 'timestamp': '...'}, 
        'memory'
    )

    # Act 2: 두 번째 호출
    resp2 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp2.rt_cd == "0"
    assert len(resp2.data) == 2
    # [수정] API 호출 횟수 확인 (과거조회 0회, 현재가조회 1회)
    assert broker.inquire_daily_itemchartprice.call_count == 0
    assert broker.get_current_price.call_count == 1

    # 3. 세 번째 호출: 캐시가 있지만 오래된 경우
    # 상황: 캐시는 20250101까지 있음. 현재 날짜가 20250105로 변경됨 (어제: 20250104)
    # 기대: 현재 구현상 날짜가 다르면 전체 재조회를 수행함
    
    # 날짜 변경 시뮬레이션 (2025-01-05)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 5, 10, 0, 0)
    
    # 캐시 히트하지만 날짜가 다름 (base_date: 20250102 != today: 20250105)
    cached_item_old = {"date": "20250101", "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100}
    cached_data_old = {'base_date': '20250102', 'data': [cached_item_old]}
    cache_manager.get_raw.return_value = (
        {'data': cached_data_old, 'timestamp': '...'}, 
        'memory'
    )
    
    # API 응답 Mock (전체 재조회)
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="", data=[{"stck_bsop_date": "20250104", "stck_clpr": "1100"}]),
        ResCommonResponse(rt_cd="0", msg1="", data=[])
    ]
    
    broker.inquire_daily_itemchartprice.call_count = 0
    broker.get_current_price.call_count = 0
    
    # Act 3
    resp3 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp3.rt_cd == "0"
    
    # 증분 업데이트가 아닌 전체 재조회이므로 API 호출 발생
    assert broker.inquire_daily_itemchartprice.call_count > 0

@pytest.mark.asyncio
async def test_get_ohlcv_full_fetch_on_large_gap(trading_service_fixture, mock_deps):
    """get_ohlcv: 캐시 갭이 100일 이상일 때 전체 재조회 수행 테스트"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    trading_service = trading_service_fixture
    
    # 1. 날짜 설정 (오늘: 2025-06-01)
    today = datetime(2025, 6, 1, 10, 0, 0)
    tm.get_current_kst_time.return_value = today
    
    # 2. 캐시 설정 (마지막 데이터: 2024-12-01, 약 6개월 전 -> 100일 이상 차이)
    # TradingService uses _daily_ohlcv_cache, not cache_manager
    cached_item = {"date": "20241201", "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100}
    cache_manager.get_raw.return_value = (
        {'data': {'base_date': '20241202', 'data': [cached_item]}, 'timestamp': '...'},
        'memory'
    )

    # 3. API 응답 설정
    # 전체 재조회 시 _fetch_past_daily_ohlcv가 호출되고, 이는 inquire_daily_itemchartprice를 호출함
    # 여기서는 루프가 한 번만 돌고 끝나도록 설정 (데이터 반환)
    full_data = [{"stck_bsop_date": "20250531", "stck_clpr": "2000"}] 
    broker.inquire_daily_itemchartprice.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="", data=full_data),
        ResCommonResponse(rt_cd="0", msg1="", data=[])
    ]
    
    # 오늘 데이터 (현재가)
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="", data={"output": {"stck_prpr": "2050", "stck_oprc": "2000", "stck_hgpr": "2100", "stck_lwpr": "1990", "acml_vol": "100"}}
    )

    # Act
    # [Fix] DynamicConfig 값을 모킹하여 테스트 환경을 고정 (기본값 100일 가정)
    with patch("services.trading_service.DynamicConfig") as mock_config:
        mock_config.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE = 100
        await trading_service.get_ohlcv("005930", period="D")
    
        # Assert
        # inquire_daily_itemchartprice가 호출되었는지 확인
        assert broker.inquire_daily_itemchartprice.called
        
        # 호출 인자 확인
        # 증분 업데이트라면 start_date가 last_cached_date + 1일 ("20241202") 이어야 함.
        # 전체 재조회라면 start_date가 yesterday(20250531) - 100일 ("20250220") 이어야 함.
        # 루프가 돌아서 여러 번 호출될 수 있으므로 첫 번째 호출을 확인
        first_call = broker.inquire_daily_itemchartprice.call_args_list[0]
        _, kwargs = first_call
        start_date_arg = kwargs['start_date']
        
        # 증분 업데이트가 아님을 확인 (20241202가 아님)
        assert start_date_arg != "20241202"
        
        # 전체 재조회 로직(어제 기준 100일 전)을 따랐는지 확인
        # 20250531 - 100 days = 20250220
        assert start_date_arg == "20250220"

@pytest.mark.asyncio
async def test_get_ohlcv_skip_today_api_if_cached_and_closed(trading_service_fixture, mock_deps):
    """장 마감 후이고 캐시에 오늘 데이터가 있으면 오늘 데이터 API 호출 스킵"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    trading_service = trading_service_fixture
    
    # 1. 장 마감 시간 설정 (18:00)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 18, 0, 0)
    tm.is_market_open.return_value = False
    
    # 2. 캐시 설정 (오늘 20250102 데이터 포함)
    today_str = "20250102"
    cached_data = [
        {"date": "20250101", "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100},
        {"date": today_str, "close": 1010.0, "open": 1010.0, "high": 1010.0, "low": 1010.0, "volume": 100}
    ]
    cache_manager.get_raw.return_value = (
        {'data': {'base_date': today_str, 'data': cached_data}, 'timestamp': '...'},
        'memory'
    )
    
    # 3. 호출
    resp = await trading_service.get_ohlcv("005930", period="D")
    
    # 4. 검증
    assert resp.rt_cd == "0"
    assert len(resp.data) == 2
    # 오늘 데이터 API 호출이 없어야 함
    broker.get_current_price.assert_not_called()
    broker.inquire_daily_itemchartprice.assert_not_called()

@pytest.mark.asyncio
async def test_get_ohlcv_during_market_open_uses_cache_for_past(trading_service_fixture, mock_deps):
    """장 중일 때 과거 데이터는 캐시를 사용하고 오늘 데이터만 API 호출하는지 검증"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    trading_service = trading_service_fixture
    
    # 1. 장 중 시간 설정 (2025-01-02 10:00:00)
    today_dt = datetime(2025, 1, 2, 10, 0, 0)
    tm.get_current_kst_time.return_value = today_dt
    tm.is_market_open.return_value = True
    
    # 2. 캐시 설정 (어제인 2025-01-01까지 데이터 있음)
    yesterday_str = "20250101"
    cached_past_data = [
        {"date": "20241231", "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100},
        {"date": yesterday_str, "close": 1010.0, "open": 1010.0, "high": 1010.0, "low": 1010.0, "volume": 100}
    ]
    cache_manager.get_raw.return_value = (
        {'data': {'base_date': '20250102', 'data': cached_past_data}, 'timestamp': '...'},
        'memory'
    )
    
    # 3. 현재가 API Mock (오늘 데이터)
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data={"output": {"stck_oprc": "1020", "stck_hgpr": "1030", "stck_lwpr": "1010", "stck_prpr": "1025", "acml_vol": "500"}}
    )
    
    # 4. 실행
    resp = await trading_service.get_ohlcv("005930", period="D")
    
    # 5. 검증
    assert resp.rt_cd == "0"
    # 데이터 개수: 과거(2) + 오늘(1) = 3
    assert len(resp.data) == 3
    assert resp.data[-1]['date'] == "20250102" # 오늘 날짜
    
    # 핵심 검증: 과거 데이터 API는 호출되지 않아야 함 (캐시 사용)
    broker.inquire_daily_itemchartprice.assert_not_called()
    
    # 핵심 검증: 오늘 데이터(현재가) API는 호출되어야 함 (실시간 반영)
    broker.get_current_price.assert_called_once()

@pytest.mark.asyncio
async def test_get_ohlcv_current_price_exception(trading_service_fixture, mock_deps):
    """get_ohlcv: 현재가 조회 중 예외 발생 시 무시하고 과거 데이터만 반환"""
    broker = mock_deps.broker
    cache_manager = mock_deps.cache_manager
    service = trading_service_fixture
    
    # 과거 데이터
    past_data = [{"stck_bsop_date": "20250101", "stck_clpr": "1000"}]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 조회 예외
    broker.get_current_price.side_effect = Exception("API Error")
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert resp.rt_cd == "0"
    assert len(resp.data) == 1 # 과거 데이터만 있음
    assert resp.data[0]['date'] == "20250101"

@pytest.mark.asyncio
async def test_get_ohlcv_weekend_filtering(trading_service_fixture, mock_deps):
    """get_ohlcv: 주말(토/일)에는 현재가 API가 데이터를 반환해도 제외"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    service = trading_service_fixture
    
    # 토요일로 설정 (2025-01-04)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 4, 10, 0, 0)
    
    # 과거 데이터 (금요일까지)
    past_data = [{"stck_bsop_date": "20250103", "stck_clpr": "1000"}]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 API는 금요일 데이터를 반환함
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1020", "stck_lwpr": "990", 
        "stck_prpr": "1010", "acml_vol": "500"
    }
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": today_output})
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert len(resp.data) == 1
    assert resp.data[0]['date'] == "20250103" # 1월 4일 데이터는 없음

@pytest.mark.asyncio
async def test_get_ohlcv_duplicate_filtering(trading_service_fixture, mock_deps):
    """get_ohlcv: 휴장일 등으로 현재가 데이터가 과거 마지막 데이터와 동일하면 중복 제거"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    service = trading_service_fixture
    
    # 평일 (2025-01-02 목요일)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    
    # 과거 데이터 (1월 1일)
    past_data = [{"stck_bsop_date": "20250101", "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000", "stck_clpr": "1000", "acml_vol": "100"}]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 API가 1월 1일 데이터와 동일한 값을 반환
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000", 
        "stck_prpr": "1000", "acml_vol": "100"
    }
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": today_output})
    
    resp = await service.get_ohlcv("005930", period="D")
    
    # 중복 제거되어 1개만 있어야 함
    assert len(resp.data) == 1
    assert resp.data[0]['date'] == "20250101"

@pytest.mark.asyncio
async def test_get_ohlcv_object_output(trading_service_fixture, mock_deps):
    """get_ohlcv: 현재가 API 응답이 객체(dataclass 등)일 때 처리"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    cache_manager = mock_deps.cache_manager
    service = trading_service_fixture
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=[])
    
    class MockOutput:
        stck_oprc = "2000"
        stck_hgpr = "2100"
        stck_lwpr = "1900"
        stck_prpr = "2050"
        acml_vol = "1000"
        
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="", data={"output": MockOutput()}
    )
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert len(resp.data) == 1
    assert resp.data[0]['close'] == 2050.0

@pytest.mark.asyncio
async def test_get_current_stock_price(trading_service_fixture, mock_deps):
    """현재가 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={})
    
    resp = await trading_service_fixture.get_current_stock_price("005930")
    assert resp.rt_cd == "0"
    broker.get_current_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_ohlcv_range(trading_service_fixture, mock_deps):
    """기간별 OHLCV 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    
    resp = await trading_service_fixture.get_ohlcv_range("005930", start_date="20250101", end_date="20250110")
    assert resp.rt_cd == "0"
    broker.inquire_daily_itemchartprice.assert_awaited_with(stock_code="005930", start_date="20250101", end_date="20250110", fid_period_div_code="D")

@pytest.mark.asyncio
async def test_get_asking_price(trading_service_fixture, mock_deps):
    """호가 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_asking_price = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_asking_price("005930")
    assert resp.rt_cd == "0"
    broker.get_asking_price.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_time_concluded_prices(trading_service_fixture, mock_deps):
    """시간대별 체결가 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_time_concluded_prices = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_time_concluded_prices("005930")
    assert resp.rt_cd == "0"
    broker.get_time_concluded_prices.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_get_etf_info(trading_service_fixture, mock_deps):
    """ETF 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_etf_info = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_etf_info("005930")
    assert resp.rt_cd == "0"
    broker.get_etf_info.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_websocket_connection_methods(trading_service_fixture, mock_deps):
    """웹소켓 연결/해제 위임 테스트"""
    broker = mock_deps.broker
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
    tm = mock_deps.tm
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
    tm = mock_deps.tm
    
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
    service = trading_service_fixture
    
    broker.get_all_stock_code_list.side_effect = Exception("API Error")
    
    resp = await service.get_all_stocks_code()
    
    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "전체 종목 코드 조회 실패" in resp.msg1

@pytest.mark.asyncio
async def test_get_all_stocks_code_invalid_type(trading_service_fixture, mock_deps):
    """전체 종목 코드 조회 반환 타입 오류 테스트"""
    broker = mock_deps.broker
    service = trading_service_fixture
    
    broker.get_all_stock_code_list = AsyncMock(return_value="Not a list")
    
    resp = await service.get_all_stocks_code()
    
    assert resp.rt_cd == ErrorCode.PARSING_ERROR.value
    assert "비정상 응답 형식" in resp.msg1

@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_with_start_date(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: start_date 지정 시 단일 호출 테스트"""
    broker = mock_deps.broker
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
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
    broker = mock_deps.broker
    broker.unsubscribe_program_trading = AsyncMock(return_value=True)
    
    result = await trading_service_fixture.unsubscribe_program_trading("005930")
    
    assert result is True
    broker.unsubscribe_program_trading.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_invalid_code(trading_service_fixture, mock_deps):
    """프로그램 매매 구독 해지 시 종목 코드 누락 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.unsubscribe_program_trading = AsyncMock()
    
    result = await trading_service_fixture.unsubscribe_program_trading("")
    
    assert result is False
    broker.unsubscribe_program_trading.assert_not_awaited()
    logger.warning.assert_called_with("프로그램 매매 구독 해지를 위한 종목 코드가 누락되었습니다.")

@pytest.mark.asyncio
async def test_unsubscribe_program_trading_exception(trading_service_fixture, mock_deps):
    """프로그램 매매 구독 해지 중 예외 발생 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.unsubscribe_program_trading = AsyncMock(side_effect=Exception("Network Error"))
    
    result = await trading_service_fixture.unsubscribe_program_trading("005930")
    
    assert result is False
    broker.unsubscribe_program_trading.assert_awaited_once_with("005930")
    logger.exception.assert_called_with("프로그램 매매 구독 해지 중 오류 발생: Network Error")

@pytest.mark.asyncio
async def test_get_price_summary(trading_service_fixture, mock_deps):
    """가격 요약 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_price_summary = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_price_summary("005930")
    
    assert resp.rt_cd == "0"
    broker.get_price_summary.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 종목 요약 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_info_by_code(trading_service_fixture, mock_deps):
    """종목 상세 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_info_by_code("005930")
    
    assert resp.rt_cd == "0"
    broker.get_stock_info_by_code.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 종목 상세 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_account_balance(trading_service_fixture, mock_deps):
    """계좌 잔고 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_account_balance = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_account_balance()
    
    assert resp.rt_cd == "0"
    broker.get_account_balance.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_stock_conclusion(trading_service_fixture, mock_deps):
    """체결 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_conclusion = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_conclusion("005930")
    
    assert resp.rt_cd == "0"
    broker.get_stock_conclusion.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 체결 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_multi_price(trading_service_fixture, mock_deps):
    """복수종목 현재가 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_multi_price = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    codes = ["005930", "000660"]
    
    resp = await trading_service_fixture.get_multi_price(codes)
    
    assert resp.rt_cd == "0"
    broker.get_multi_price.assert_awaited_once_with(codes)
    logger.info.assert_called_with(f"Trading_Service - 복수종목 현재가 조회 요청 ({len(codes)}종목)")

@pytest.mark.asyncio
async def test_get_financial_ratio(trading_service_fixture, mock_deps):
    """재무비율 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_financial_ratio = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_financial_ratio("005930")
    
    assert resp.rt_cd == "0"
    broker.get_financial_ratio.assert_awaited_once_with("005930")
    logger.info.assert_called_with("Service - 005930 재무비율 조회 요청")

@pytest.mark.asyncio
async def test_get_intraday_minutes_today(trading_service_fixture, mock_deps):
    """당일 분봉 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.inquire_time_itemchartprice = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_intraday_minutes_today(stock_code="005930", input_hour_1="120000")
    
    assert resp.rt_cd == "0"
    broker.inquire_time_itemchartprice.assert_awaited_once_with(
        stock_code="005930", input_hour_1="120000", pw_data_incu_yn="Y", etc_cls_code="0"
    )

@pytest.mark.asyncio
async def test_get_intraday_minutes_by_date_real(trading_service_fixture, mock_deps):
    """일별 분봉 조회 (실전투자) 테스트"""
    broker = mock_deps.broker
    env = mock_deps.env
    env.is_paper_trading = False
    broker.inquire_time_dailychartprice = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_intraday_minutes_by_date(stock_code="005930", input_date_1="20250101")
    
    assert resp.rt_cd == "0"
    broker.inquire_time_dailychartprice.assert_awaited_once_with(
        stock_code="005930", input_date_1="20250101", input_hour_1="", pw_data_incu_yn="Y", fake_tick_incu_yn=""
    )

@pytest.mark.asyncio
async def test_get_intraday_minutes_by_date_paper(trading_service_fixture, mock_deps):
    """일별 분봉 조회 (모의투자) 테스트 - 미지원"""
    broker = mock_deps.broker
    env = mock_deps.env
    env.is_paper_trading = True
    
    resp = await trading_service_fixture.get_intraday_minutes_by_date(stock_code="005930", input_date_1="20250101")
    
    assert resp.rt_cd == ErrorCode.API_ERROR.value
    assert "모의투자 미지원" in resp.msg1
    broker.inquire_time_dailychartprice.assert_not_called()

@pytest.mark.asyncio
async def test_get_top_rise_fall_stocks(trading_service_fixture, mock_deps):
    """상승/하락 순위 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_top_rise_fall_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    # Rise
    await trading_service_fixture.get_top_rise_fall_stocks(rise=True)
    broker.get_top_rise_fall_stocks.assert_awaited_with(True)
    logger.info.assert_called_with("Service - 상승률 상위 종목 조회 요청")
    
    # Fall
    await trading_service_fixture.get_top_rise_fall_stocks(rise=False)
    broker.get_top_rise_fall_stocks.assert_awaited_with(False)
    logger.info.assert_called_with("Service - 하락률 상위 종목 조회 요청")

@pytest.mark.asyncio
async def test_get_top_volume_stocks(trading_service_fixture, mock_deps):
    """거래량 상위 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_top_volume_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    await trading_service_fixture.get_top_volume_stocks()
    
    broker.get_top_volume_stocks.assert_awaited_once()
    logger.info.assert_called_with("Service - 거래량 상위 종목 조회 요청")

@pytest.mark.asyncio
async def test_get_name_by_code(trading_service_fixture, mock_deps):
    """종목명 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_name_by_code = AsyncMock(return_value="삼성전자")
    
    name = await trading_service_fixture.get_name_by_code("005930")
    
    assert name == "삼성전자"
    broker.get_name_by_code.assert_awaited_once_with("005930")

@pytest.mark.asyncio
async def test_inquire_daily_itemchartprice(trading_service_fixture, mock_deps):
    """일별 차트 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.inquire_daily_itemchartprice = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.inquire_daily_itemchartprice("005930", "20250101", "20250110")
    
    assert resp.rt_cd == "0"
    broker.inquire_daily_itemchartprice.assert_awaited_once_with(
        stock_code="005930", start_date="20250101", end_date="20250110", fid_period_div_code="D"
    )

@pytest.mark.asyncio
async def test_handle_realtime_stream_exception(trading_service_fixture, mock_deps):
    """실시간 스트림 처리 중 예외 발생 시 로그 기록 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.connect_websocket.side_effect = Exception("Connection Failed")

    # finally 블록에서 호출되는 메서드들이 await 가능해야 함
    broker.unsubscribe_realtime_price = AsyncMock()
    broker.unsubscribe_realtime_quote = AsyncMock()
    broker.disconnect_websocket = AsyncMock()
    
    await trading_service_fixture.handle_realtime_stream(["005930"], ["price"], 1)
    
    logger.exception.assert_called_once()
    assert "실시간 스트림 처리 중 오류 발생" in logger.exception.call_args[0][0]

@pytest.mark.asyncio
async def test_get_latest_trading_date_success(trading_service_fixture, mock_deps):
    """get_latest_trading_date 성공 케이스 테스트 (Delegation)"""
    service = trading_service_fixture
    
    # Mock MarketDateManager
    mock_mdm = AsyncMock()
    mock_mdm.get_latest_trading_date.return_value = "20250103"
    service._market_date_manager = mock_mdm
    
    result = await service.get_latest_trading_date()
    
    assert result == "20250103"
    mock_mdm.get_latest_trading_date.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_latest_trading_date_none(trading_service_fixture, mock_deps):
    """get_latest_trading_date: 매니저가 None 반환 시 (Delegation)"""
    service = trading_service_fixture
    
    mock_mdm = AsyncMock()
    mock_mdm.get_latest_trading_date.return_value = None
    service._market_date_manager = mock_mdm
    
    result = await service.get_latest_trading_date()
    
    assert result is None
    mock_mdm.get_latest_trading_date.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_latest_trading_date_no_manager(trading_service_fixture, mock_deps):
    """get_latest_trading_date: 매니저 미설정 시 None 반환"""
    logger = mock_deps.logger
    service = trading_service_fixture
    service._market_date_manager = None

    result = await service.get_latest_trading_date()
    
    assert result is None
    logger.warning.assert_called_once()

class TestGetCurrentUpperLimitStocksAttributeError(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.trading_service = TradingService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=MagicMock(),
            logger=self.mock_logger,
            time_manager=MagicMock()
        )

    async def test_get_current_upper_limit_stocks_attribute_error(self):
        """ResFluctuation 객체가 아닌 데이터(속성 접근 불가)가 들어올 때 예외 처리 테스트"""
        # dict는 .stck_shrn_iscd 속성이 없으므로 AttributeError 발생 -> except 블록 진입 -> 스킵
        rise_stocks = [{"stck_shrn_iscd": "005930", "stck_prpr": "10000"}]
        
        result = await self.trading_service.get_current_upper_limit_stocks(rise_stocks)
        
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 0
        self.mock_logger.warning.assert_called()

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

    def _create_dummy_fluctuation(self, overrides=None):
        data = {
            "stck_shrn_iscd": "000000", "data_rank": "1", "hts_kor_isnm": "Dummy", "stck_prpr": "1000",
            "prdy_vrss": "10", "prdy_vrss_sign": "2", "prdy_ctrt": "1.0", "acml_vol": "10000",
            "stck_hgpr": "1100", "hgpr_hour": "100000", "acml_hgpr_date": "20230101", "stck_lwpr": "900",
            "lwpr_hour": "090000", "acml_lwpr_date": "20230101", "lwpr_vrss_prpr_rate": "10.0",
            "dsgt_date_clpr_vrss_prpr_rate": "0.0", "cnnt_ascn_dynu": "1", "hgpr_vrss_prpr_rate": "-10.0",
            "cnnt_down_dynu": "0", "oprc_vrss_prpr_sign": "2", "oprc_vrss_prpr": "100",
            "oprc_vrss_prpr_rate": "10.0", "prd_rsfl": "100", "prd_rsfl_rate": "10.0"
        }
        if overrides:
            data.update(overrides)
        return ResFluctuation.from_dict(data)

    async def test_get_current_upper_limit_stocks_success(self):
        rise_stocks = [
            self._create_dummy_fluctuation({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "30000",
                "prdy_ctrt": "29.99",  # 상한가 조건 충족
                "prdy_vrss": "2999",
                "data_rank": "1",
            }),
            self._create_dummy_fluctuation({
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
            self._create_dummy_fluctuation({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "종목A",
                "stck_prpr": "10000",
                "prdy_ctrt": "5.0",  # 상한가 아님
                "prdy_vrss": "500",
                "data_rank": "1",
            }),
            self._create_dummy_fluctuation({
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
            self._create_dummy_fluctuation({
                "stck_shrn_iscd": "000660",
                "hts_kor_isnm": "종목A",
                "stck_prpr": "N/A",  # ← 고의로 잘못된 값
                "prdy_ctrt": "30.0",  # (의미 없음, 위에서 이미 터짐)
                "prdy_vrss": "0",
                "data_rank": "1",
            }),
            # 2) 등락률이 숫자가 아님 → float("notnum")에서 예외
            self._create_dummy_fluctuation({
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
        self.mock_logger.exception.assert_called_once()


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

    def _create_dummy_fluctuation(self, overrides=None):
        data = {
            "stck_shrn_iscd": "000000", "data_rank": "1", "hts_kor_isnm": "Dummy", "stck_prpr": "1000",
            "prdy_vrss": "10", "prdy_vrss_sign": "2", "prdy_ctrt": "1.0", "acml_vol": "10000",
            "stck_hgpr": "1100", "hgpr_hour": "100000", "acml_hgpr_date": "20230101", "stck_lwpr": "900",
            "lwpr_hour": "090000", "acml_lwpr_date": "20230101", "lwpr_vrss_prpr_rate": "10.0",
            "dsgt_date_clpr_vrss_prpr_rate": "0.0", "cnnt_ascn_dynu": "1", "hgpr_vrss_prpr_rate": "-10.0",
            "cnnt_down_dynu": "0", "oprc_vrss_prpr_sign": "2", "oprc_vrss_prpr": "100",
            "oprc_vrss_prpr_rate": "10.0", "prd_rsfl": "100", "prd_rsfl_rate": "10.0"
        }
        if overrides:
            data.update(overrides)
        return ResFluctuation.from_dict(data)

    async def test_get_price_summary_returns_none_skips_stock(self):
        rise_stocks = [
            self._create_dummy_fluctuation({
                "stck_shrn_iscd": "CODEF",
                "hts_kor_isnm": "종목F",
                "stck_prpr": "30770",
                "prdy_ctrt": "28.0",  # ← 상한가 조건 미충족 → 스킵
                "prdy_vrss": "0",
            }),
            self._create_dummy_fluctuation({
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
