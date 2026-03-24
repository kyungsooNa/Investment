import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta
from services.market_data_service import MarketDataService
from common.types import ErrorCode, ResCommonResponse, ResFluctuation, ResBasicStockInfo, ResStockFullInfoApiOutput
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
    cache_store = MagicMock()  # Added cache_store mock
    stock_repo = MagicMock()
    stock_repo.get_current_price.return_value = None
    stock_repo.get_stock_data = AsyncMock(return_value=None)
    stock_repo.get_latest_daily_snapshot = AsyncMock(return_value=None)
    stock_repo.upsert_ohlcv = AsyncMock()

    return SimpleNamespace(
        broker=broker,
        env=env,
        tm=tm,
        logger=logger,
        cache_store=cache_store,
        stock_repo=stock_repo
    )

@pytest.fixture
def trading_service_fixture(mock_deps):
    service = MarketDataService(
        broker_api_wrapper=mock_deps.broker,
        env=mock_deps.env,
        market_clock=mock_deps.tm,
        logger=mock_deps.logger,
        cache_store=mock_deps.cache_store,
        stock_repository=mock_deps.stock_repo
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
    """get_ohlcv 메서드의 로컬 저장소 연동 및 백필 동작 검증"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    trading_service = trading_service_fixture
    trading_service._mcs = AsyncMock()
    trading_service._mcs.is_market_open_now.return_value = True
    
    # 1. 초기 상태: 로컬 데이터 없음 (0건) -> API 백필 수행
    stock_repo.get_stock_data.return_value = None
    
    # 과거 데이터 (백필 시 600건 이상 채워짐을 모사)
    base_date = datetime(2023, 1, 1)
    past_data = [{"stck_bsop_date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "stck_clpr": "1000"} for i in range(605)]
    broker.inquire_daily_itemchartprice.side_effect = [ResCommonResponse(rt_cd="0", msg1="", data=past_data), ResCommonResponse(rt_cd="0", msg1="", data=[])]
    
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1020", "stck_lwpr": "990", 
        "stck_prpr": "1010", "acml_vol": "500"
    }
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="", data={"output": today_output}
    )
    
    resp1 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp1.rt_cd == "0"
    # 백필 쿼리 실행 확인
    assert broker.inquire_daily_itemchartprice.call_count > 0
    # 로컬 DB 저장소 호출 확인
    stock_repo.upsert_ohlcv.assert_called()
    
    broker.inquire_daily_itemchartprice.call_count = 0 # 카운트 리셋
    broker.get_current_price.call_count = 0
    
    # 2. 두 번째 호출: 로컬 데이터가 충분함 (600건 이상)
    # 과거 데이터 백필 API 호출은 스킵하고, 당일 데이터 API만 호출
    base_date = datetime(2023, 1, 1)
    stock_repo.get_stock_data.return_value = {
        "ohlcv": [{"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100} for i in range(605)]
    }

    resp2 = await trading_service.get_ohlcv("005930", period="D")
    
    assert resp2.rt_cd == "0"
    # 로컬 데이터 활용으로 백필 생략 검증
    assert broker.inquire_daily_itemchartprice.call_count == 0
    # 당일 실시간 캔들 병합
    assert broker.get_current_price.call_count == 1

@pytest.mark.asyncio
async def test_get_ohlcv_full_fetch_on_short_data(trading_service_fixture, mock_deps):
    """get_ohlcv: 로컬 데이터가 600건 미만일 때 전체 백필 수행"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    trading_service = trading_service_fixture
    
    # 로컬 데이터가 300건밖에 없음
    base_date = datetime(2024, 1, 1)
    stock_repo.get_stock_data.return_value = {
        "ohlcv": [{"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100} for i in range(300)]
    }
    
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=[])
    
    await trading_service.get_ohlcv("005930", period="D")
    
    # 600건 미만이므로 백필을 위해 API가 호출됨
    assert broker.inquire_daily_itemchartprice.called

@pytest.mark.asyncio
async def test_get_ohlcv_skip_today_api_if_cached_and_closed(trading_service_fixture, mock_deps):
    """장 마감 후이고 캐시에 오늘 데이터가 있으면 오늘 데이터 API 호출 스킵"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    trading_service = trading_service_fixture
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 18, 0, 0)
    trading_service._mcs = AsyncMock()
    trading_service._mcs.is_market_open_now.return_value = False
    
    today_str = "20250102"
    base_date = datetime(2023, 1, 1)
    cached_data = [{"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "close": 1000.0} for i in range(600)]
    cached_data.append({"date": today_str, "close": 1010.0, "open": 1010.0, "high": 1010.0, "low": 1010.0, "volume": 100})
    stock_repo.get_stock_data.return_value = {"ohlcv": cached_data}
    
    # 3. 호출
    resp = await trading_service.get_ohlcv("005930", period="D")
    
    # 4. 검증
    assert resp.rt_cd == "0"
    assert len(resp.data) > 600
    # 오늘 데이터 API 호출이 없어야 함
    broker.get_current_price.assert_not_called()
    broker.inquire_daily_itemchartprice.assert_not_called()

@pytest.mark.asyncio
async def test_get_ohlcv_during_market_open_uses_cache_for_past(trading_service_fixture, mock_deps):
    """장 중일 때 과거 데이터는 캐시를 사용하고 오늘 데이터만 API 호출하는지 검증"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    trading_service = trading_service_fixture
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    trading_service._mcs = AsyncMock()
    trading_service._mcs.is_market_open_now.return_value = True
    
    # 2. 캐시 설정 (어제인 2025-01-01까지 데이터 있음)
    yesterday_str = "20250101"
    base_date = datetime(2023, 1, 1)
    cached_past_data = [{"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100} for i in range(600)]
    cached_past_data.append({"date": yesterday_str, "close": 1010.0, "open": 1010.0, "high": 1010.0, "low": 1010.0, "volume": 100})
    stock_repo.get_stock_data.return_value = {"ohlcv": cached_past_data}
    
    # 3. 현재가 API Mock (오늘 데이터)
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", 
        data={"output": {"stck_oprc": "1020", "stck_hgpr": "1030", "stck_lwpr": "1010", "stck_prpr": "1025", "acml_vol": "500"}}
    )
    
    # 4. 실행
    resp = await trading_service.get_ohlcv("005930", period="D")
    
    # 5. 검증
    assert resp.rt_cd == "0"
    assert len(resp.data) > 600
    assert resp.data[-1]['date'] == "20250102" # 오늘 날짜
    
    # 핵심 검증: 과거 데이터 API는 호출되지 않아야 함 (캐시 사용)
    broker.inquire_daily_itemchartprice.assert_not_called()
    
    # 핵심 검증: 오늘 데이터(현재가) API는 호출되어야 함 (실시간 반영)
    broker.get_current_price.assert_called_once()

@pytest.mark.asyncio
async def test_get_ohlcv_current_price_exception(trading_service_fixture, mock_deps):
    """get_ohlcv: 현재가 조회 중 예외 발생 시 무시하고 과거 데이터만 반환"""
    broker = mock_deps.broker
    service = trading_service_fixture
    mock_deps.stock_repo.get_stock_data.return_value = None
    
    # 과거 데이터
    base_date = datetime(2023, 1, 1)
    past_data = [{"stck_bsop_date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "stck_clpr": "1000"} for i in range(600)]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 조회 예외
    broker.get_current_price.side_effect = Exception("API Error")
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert resp.rt_cd == "0"
    assert len(resp.data) == 600

@pytest.mark.asyncio
async def test_get_ohlcv_weekend_filtering(trading_service_fixture, mock_deps):
    """get_ohlcv: 주말(토/일)에는 현재가 API가 데이터를 반환해도 제외"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    service = trading_service_fixture
    mock_deps.stock_repo.get_stock_data.return_value = None
    
    # 토요일로 설정 (2025-01-04)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 4, 10, 0, 0)
    
    # 과거 데이터 (금요일까지)
    base_date = datetime(2023, 1, 1)
    past_data = [{"stck_bsop_date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "stck_clpr": "1000"} for i in range(599)]
    past_data.append({"stck_bsop_date": "20250103", "stck_clpr": "1000"})
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 API는 금요일 데이터를 반환함
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1020", "stck_lwpr": "990", 
        "stck_prpr": "1010", "acml_vol": "500"
    }
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": today_output})
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert resp.data[-1]['date'] == "20250103" # 1월 4일 데이터는 없음

@pytest.mark.asyncio
async def test_get_ohlcv_duplicate_filtering(trading_service_fixture, mock_deps):
    """get_ohlcv: 휴장일 등으로 현재가 데이터가 과거 마지막 데이터와 동일하면 중복 제거"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    service = trading_service_fixture
    mock_deps.stock_repo.get_stock_data.return_value = None
    
    # 평일 (2025-01-02 목요일)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    
    # 과거 데이터 (1월 1일)
    base_date = datetime(2023, 1, 1)
    past_data = [{"stck_bsop_date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000", "stck_clpr": "1000", "acml_vol": "100"} for i in range(599)]
    past_data.append({"stck_bsop_date": "20250101", "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000", "stck_clpr": "1000", "acml_vol": "100"})
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)
    
    # 현재가 API가 1월 1일 데이터와 동일한 값을 반환
    today_output = {
        "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000", 
        "stck_prpr": "1000", "acml_vol": "100"
    }
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="", data={"output": today_output})
    
    resp = await service.get_ohlcv("005930", period="D")
    
    assert resp.data[-1]['date'] == "20250101"

@pytest.mark.asyncio
async def test_get_ohlcv_object_output(trading_service_fixture, mock_deps):
    """get_ohlcv: 현재가 API 응답이 객체(dataclass 등)일 때 처리"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    service = trading_service_fixture
    mock_deps.stock_repo.get_stock_data.return_value = None
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = True
    base_date = datetime(2023, 1, 1)
    past_data = [{"stck_bsop_date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "stck_clpr": "1000"} for i in range(600)]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="", data=past_data)

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
    
    assert resp.data[-1]['close'] == 2050.0

@pytest.mark.asyncio
async def test_get_current_price_cache_hit(trading_service_fixture, mock_deps):
    """현재가 조회: StockRepository에 캐시가 있을 경우 API 호출 생략"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    
    # 3초 단기 캐시 Hit 설정
    cached_data = {"output": {"stck_prpr": "80000", "acml_vol": "100"}}
    stock_repo.get_current_price.return_value = cached_data
    
    resp = await trading_service_fixture.get_current_price("005930")
    
    assert resp.rt_cd == "0"
    assert resp.data == cached_data
    stock_repo.get_current_price.assert_called_once_with("005930", max_age_sec=3.0, count_stats=True, caller="unknown")
    broker.get_current_price.assert_not_called()

@pytest.mark.asyncio
async def test_get_current_price_cache_miss(trading_service_fixture, mock_deps):
    """현재가 조회: 캐시 Miss + 장 중 → DB 건너뛰고 API 호출 후 StockRepository에 갱신"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = True  # 장 중

    api_data = {"output": {"stck_prpr": "80000", "acml_vol": "100"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    stock_repo.get_latest_daily_snapshot.assert_not_called()
    broker.get_current_price.assert_awaited_once_with("005930")
    stock_repo.set_current_price.assert_called_once_with("005930", api_data)


@pytest.mark.asyncio
async def test_get_current_price_db_fallback_when_market_closed(trading_service_fixture, mock_deps):
    """장 마감 + LRU miss → daily_prices DB 조회 → API 호출 없이 반환 및 LRU 적재"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    db_snapshot = {"output": {"stck_prpr": "70000"}, "_source": "daily_snapshot", "_trade_date": "20260318"}
    stock_repo.get_latest_daily_snapshot.return_value = db_snapshot
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False  # 장 마감

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    assert resp.msg1 == "성공(DB)"
    assert isinstance(resp.data["output"], ResStockFullInfoApiOutput)
    assert resp.data["output"].stck_prpr == "70000"
    assert resp.data["_source"] == "daily_snapshot"
    broker.get_current_price.assert_not_called()
    stock_repo.get_latest_daily_snapshot.assert_called_once_with("005930")
    stock_repo.set_current_price.assert_called_once_with("005930", resp.data)


@pytest.mark.asyncio
async def test_get_current_price_db_miss_then_api_when_market_closed(trading_service_fixture, mock_deps):
    """장 마감 + LRU miss + DB miss → API 호출"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    stock_repo.get_latest_daily_snapshot.return_value = None
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False  # 장 마감

    api_data = {"output": {"stck_prpr": "70000"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    broker.get_current_price.assert_awaited_once_with("005930")
    stock_repo.set_current_price.assert_called_once_with("005930", api_data)


@pytest.mark.asyncio
async def test_get_current_price_skip_db_when_market_open(trading_service_fixture, mock_deps):
    """장 중 + LRU miss → DB 확인 없이 바로 API 호출"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = True  # 장 중

    api_data = {"output": {"stck_prpr": "70000"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    await service.get_current_price("005930")

    stock_repo.get_latest_daily_snapshot.assert_not_called()

@pytest.mark.asyncio
async def test_get_ohlcv_range(trading_service_fixture, mock_deps):
    """기간별 OHLCV 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    
    resp = await trading_service_fixture.get_ohlcv_range("005930", start_date="20250101", end_date="20250110")
    assert resp.rt_cd == "0"
    broker.inquire_daily_itemchartprice.assert_awaited_with("005930", start_date="20250101", end_date="20250110", fid_period_div_code="D")

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
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
    ]
    
    # limit 200이면 (200*1.5//100)+1 = 4개의 태스크를 생성합니다.
    result = await service.get_recent_daily_ohlcv("005930", limit=200)
    
    assert len(result) == 5
    assert result[0]['date'] == "20250101"
    assert result[-1]['date'] == "20250105"
    assert broker.inquire_daily_itemchartprice.call_count == 4

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
    
    # limit 60이면 (60*1.5//100)+1 = 1개의 태스크만 생성 (하지만 에러 발생 루틴을 타기 위해 limit을 100으로 주어 2개의 태스크 생성)
    result = await service.get_recent_daily_ohlcv("005930", limit=100)
    
    assert len(result) == 1
    assert result[0]['date'] == "20250105"
    assert broker.inquire_daily_itemchartprice.call_count == 2

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
    
    # limit 150이면 (150*1.5//100)+1 = 3개의 태스크 생성
    result = await service.get_recent_daily_ohlcv("005930", limit=150)
    
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
async def test_get_price_summary(trading_service_fixture, mock_deps):
    """가격 요약 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_price_summary = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_price_summary("005930")
    
    assert resp.rt_cd == "0"
    broker.get_price_summary.assert_awaited_once_with("005930")
    logger.info.assert_called_with("MarketDataService - 005930 종목 요약 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_info_by_code(trading_service_fixture, mock_deps):
    """종목 상세 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_info_by_code("005930")
    
    assert resp.rt_cd == "0"
    broker.get_stock_info_by_code.assert_awaited_once_with("005930")
    logger.info.assert_called_with("MarketDataService - 005930 종목 상세 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_conclusion(trading_service_fixture, mock_deps):
    """체결 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_conclusion = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_conclusion("005930")
    
    assert resp.rt_cd == "0"
    broker.get_stock_conclusion.assert_awaited_once_with("005930")
    logger.info.assert_called_with("MarketDataService - 005930 체결 정보 조회 요청")

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
    logger.info.assert_called_with(f"MarketDataService - 복수종목 현재가 조회 요청 ({len(codes)}종목)")

@pytest.mark.asyncio
async def test_get_financial_ratio(trading_service_fixture, mock_deps):
    """재무비율 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_financial_ratio = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_financial_ratio("005930")
    
    assert resp.rt_cd == "0"
    broker.get_financial_ratio.assert_awaited_once_with("005930")
    logger.info.assert_called_with("MarketDataService - 005930 재무비율 조회")

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
    logger.info.assert_called_with("MarketDataService - 상승률 상위 종목 조회 요청")

    # Fall
    await trading_service_fixture.get_top_rise_fall_stocks(rise=False)
    broker.get_top_rise_fall_stocks.assert_awaited_with(False)
    logger.info.assert_called_with("MarketDataService - 하락률 상위 종목 조회 요청")

@pytest.mark.asyncio
async def test_get_top_volume_stocks(trading_service_fixture, mock_deps):
    """거래량 상위 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_top_volume_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    await trading_service_fixture.get_top_volume_stocks()
    
    broker.get_top_volume_stocks.assert_awaited_once()
    logger.info.assert_called_with("MarketDataService - 거래량 상위 종목 조회 요청")

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
async def test_get_latest_trading_date_success(trading_service_fixture, mock_deps):
    """get_latest_trading_date 성공 케이스 테스트 (Delegation)"""
    service = trading_service_fixture
    
    # Mock MarketCalendarService
    mock_mcs = AsyncMock()
    mock_mcs.get_latest_trading_date.return_value = "20250103"
    service._mcs = mock_mcs
    
    result = await service.get_latest_trading_date()
    
    assert result == "20250103"
    mock_mcs.get_latest_trading_date.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_latest_trading_date_none(trading_service_fixture, mock_deps):
    """get_latest_trading_date: 매니저가 None 반환 시 (Delegation)"""
    service = trading_service_fixture
    
    mock_mcs = AsyncMock()
    mock_mcs.get_latest_trading_date.return_value = None
    service._mcs = mock_mcs
    
    result = await service.get_latest_trading_date()
    
    assert result is None
    mock_mcs.get_latest_trading_date.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_latest_trading_date_no_manager(trading_service_fixture):
    """get_latest_trading_date: 매니저 미설정 시 None 반환"""
    service = trading_service_fixture
    service._mcs = None

    result = await service.get_latest_trading_date()

    assert result is None

class TestGetCurrentUpperLimitStocksAttributeError(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_broker_api_wrapper = AsyncMock()
        self.mock_logger = MagicMock()
        self.trading_service = MarketDataService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=MagicMock(),
            logger=self.mock_logger,
            market_clock=MagicMock()
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
        self.trading_service = MarketDataService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            market_clock=MagicMock()
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

        self.mock_logger.info.assert_called_once_with("MarketDataService - 전체 종목 코드 조회 요청")

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

        self.trading_service = MarketDataService(
            broker_api_wrapper=self.mock_broker_api_wrapper,
            env=self.mock_env,
            logger=self.mock_logger,
            market_clock=MagicMock()
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


@pytest.mark.asyncio
async def test_get_ohlcv_after_market_close_no_today_api_call(trading_service_fixture, mock_deps):
    """장 마감 후에는 _fetch_today_ohlcv(현재가 API)를 호출하지 않고 과거 데이터만 반환"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    # 장 마감 후 (18:00)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 18, 0, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False

    # 로컬 DB에 오늘 데이터 없이 어제까지만 존재하는 상황
    base_date = datetime(2023, 1, 1)
    past_rows = [
        {"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 100}
        for i in range(600)
    ]
    # 마지막 데이터는 어제(2025-01-01)
    past_rows.append({"date": "20250101", "open": 1010.0, "high": 1020.0, "low": 1000.0, "close": 1015.0, "volume": 200})
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows}

    resp = await service.get_ohlcv("005930", period="D")

    assert resp.rt_cd == "0"
    # 현재가 API 호출 없어야 함 (장 마감 후)
    broker.get_current_price.assert_not_called()
    # 과거 일봉 API도 호출 없어야 함 (캐시 충분)
    broker.inquire_daily_itemchartprice.assert_not_called()
    # 마지막 데이터는 오늘이 아닌 어제
    assert resp.data[-1]['date'] == "20250101"


@pytest.mark.asyncio
async def test_get_ohlcv_during_market_open_calls_today_api(trading_service_fixture, mock_deps):
    """장 중에는 오늘 실시간 데이터를 현재가 API로 병합하는지 검증"""
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    # 장 중 (10:00)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = True

    # 로컬 DB에 어제까지 데이터 존재 (600건 이상)
    base_date = datetime(2023, 1, 1)
    past_rows = [
        {"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 100}
        for i in range(600)
    ]
    past_rows.append({"date": "20250101", "open": 1010.0, "high": 1020.0, "low": 1000.0, "close": 1015.0, "volume": 200})
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows}

    # 현재가 API: 오늘(2025-01-02) 데이터 반환
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_oprc": "1020", "stck_hgpr": "1030", "stck_lwpr": "1010", "stck_prpr": "1025", "acml_vol": "500"}}
    )

    resp = await service.get_ohlcv("005930", period="D")

    assert resp.rt_cd == "0"
    # 장 중에는 현재가 API 호출되어야 함
    broker.get_current_price.assert_called_once()
    # 과거 일봉 API는 캐시가 충분하므로 호출되지 않아야 함
    broker.inquire_daily_itemchartprice.assert_not_called()
    # 오늘 데이터가 병합되어 마지막 항목이 오늘 날짜여야 함
    assert resp.data[-1]['date'] == "20250102"
    assert resp.data[-1]['close'] == 1025.0
