import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta
from services.market_data_service import MarketDataService
from services.data_quality_service import DataQualityService
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
async def test_get_current_price_uses_cache_by_default(trading_service_fixture, mock_deps):
    """force_fresh=False(기본값)일 때 캐시 데이터가 있으면 broker API를 호출하지 않는다."""
    service = trading_service_fixture
    cached = {"stck_prpr": "70000", "code": "005930"}
    mock_deps.stock_repo.get_current_price.return_value = cached

    result = await service.get_current_price("005930")

    assert result.rt_cd == "0"
    assert result.msg1 == "성공(Cache)"
    mock_deps.broker.get_current_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_current_price_data_quality_invalid_response_not_cached(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    service._data_quality_service = DataQualityService()
    mock_deps.broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={"not_output": {}},
    )

    result = await service.get_current_price("005930", force_fresh=True)

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result.data["reason"] == "rest_invalid"
    mock_deps.stock_repo.set_current_price.assert_not_called()
    assert service._data_quality_service.get_violation_history(code="005930")[0]["reason"] == "rest_invalid"


@pytest.mark.asyncio
async def test_get_asking_price_data_quality_requires_output1(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    service._data_quality_service = DataQualityService()
    mock_deps.broker.get_asking_price = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="정상", data={"output": {}})
    )

    result = await service.get_asking_price("005930")

    assert result.rt_cd == ErrorCode.PARSING_ERROR.value
    assert result.data["metadata"]["missing_keys"] == ["output1"]


@pytest.mark.asyncio
async def test_get_current_price_force_fresh_skips_cache(trading_service_fixture, mock_deps):
    """force_fresh=True이면 캐시가 있어도 broker API를 직접 호출한다."""
    service = trading_service_fixture
    # 캐시에 데이터가 있어도 무시되어야 함
    mock_deps.stock_repo.get_current_price.return_value = {"stck_prpr": "70000"}
    broker_resp = ResCommonResponse(rt_cd="0", msg1="정상", data={"output": {}})
    mock_deps.broker.get_current_price.return_value = broker_resp

    result = await service.get_current_price("005930", force_fresh=True)

    mock_deps.broker.get_current_price.assert_awaited_once()
    assert result == broker_resp


@pytest.mark.asyncio
async def test_get_current_price_force_fresh_skips_db_snapshot(trading_service_fixture, mock_deps):
    """force_fresh=True이면 DB 스냅샷도 건너뛰고 broker API를 호출한다."""
    service = trading_service_fixture
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False  # 장 마감 상태
    service._mcs.get_latest_trading_date.return_value = "20260412"

    # 캐시 없음, DB 스냅샷은 있음
    mock_deps.stock_repo.get_current_price.return_value = None
    mock_deps.stock_repo.get_latest_daily_snapshot.return_value = {"stck_prpr": "70000", "_trade_date": "20260412"}

    broker_resp = ResCommonResponse(rt_cd="0", msg1="정상", data={"output": {}})
    mock_deps.broker.get_current_price.return_value = broker_resp

    result = await service.get_current_price("005930", force_fresh=True)

    # DB 스냅샷 조회가 호출되지 않아야 함
    mock_deps.stock_repo.get_latest_daily_snapshot.assert_not_awaited()
    mock_deps.broker.get_current_price.assert_awaited_once()
    assert result == broker_resp


@pytest.mark.asyncio
async def test_get_current_price_force_fresh_updates_cache(trading_service_fixture, mock_deps):
    """force_fresh=True로 broker API 호출 성공 시 결과를 캐시에 저장한다."""
    service = trading_service_fixture
    mock_deps.stock_repo.get_current_price.return_value = None
    broker_data = {"output": {"stck_prpr": "80000"}}
    broker_resp = ResCommonResponse(rt_cd="0", msg1="정상", data=broker_data)
    mock_deps.broker.get_current_price.return_value = broker_resp

    await service.get_current_price("005930", force_fresh=True)

    mock_deps.stock_repo.set_current_price.assert_called_once_with("005930", broker_data)


@pytest.mark.asyncio
async def test_get_current_price_tick_only_cache_calls_api(trading_service_fixture, mock_deps):
    """_source='tick' 인 불완전 캐시가 있을 때 API를 호출하여 완전한 데이터를 반환한다.

    시나리오:
      WebSocket 틱만 수신(API 미조회) → update_current_price()가
      {"output": {"stck_prpr": "X"}, "_source": "tick"} 최소 구조를 캐시에 저장.
      이 상태에서 현재가 조회 시 파싱 오류 방지를 위해 캐시를 무시하고 API를 호출해야 한다.
    """
    service = trading_service_fixture
    tick_cache = {"output": {"stck_prpr": "80000", "acml_vol": "1234"}, "_source": "tick"}
    mock_deps.stock_repo.get_current_price.return_value = tick_cache

    broker_data = {"output": {"stck_prpr": "80100", "stck_hgpr": "81000"}}
    broker_resp = ResCommonResponse(rt_cd="0", msg1="정상", data=broker_data)
    mock_deps.broker.get_current_price.return_value = broker_resp

    result = await service.get_current_price("039560")

    # tick-only 캐시는 무시 → broker API 호출
    mock_deps.broker.get_current_price.assert_awaited_once()
    assert result == broker_resp


@pytest.mark.asyncio
async def test_get_current_price_tick_only_cache_updates_cache_on_success(trading_service_fixture, mock_deps):
    """_source='tick' 캐시 우회 후 API 성공 시 완전한 데이터로 캐시를 갱신한다."""
    service = trading_service_fixture
    tick_cache = {"output": {"stck_prpr": "5000"}, "_source": "tick"}
    mock_deps.stock_repo.get_current_price.return_value = tick_cache

    broker_data = {"output": {"stck_prpr": "5100", "stck_hgpr": "5200"}}
    mock_deps.broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="정상", data=broker_data
    )

    await service.get_current_price("077360")

    # API 응답으로 캐시 갱신 확인
    mock_deps.stock_repo.set_current_price.assert_called_once_with("077360", broker_data)


@pytest.mark.asyncio
async def test_get_current_price_complete_cache_skips_api(trading_service_fixture, mock_deps):
    """_source 필드가 없는 정상 캐시(API 조회 결과)는 그대로 반환하고 API를 호출하지 않는다."""
    service = trading_service_fixture
    full_cache = {"output": {"stck_prpr": "70000", "stck_hgpr": "72000"}}  # _source 없음
    mock_deps.stock_repo.get_current_price.return_value = full_cache

    result = await service.get_current_price("230240")

    mock_deps.broker.get_current_price.assert_not_awaited()
    assert result.rt_cd == "0"
    assert result.msg1 == "성공(Cache)"


@pytest.mark.asyncio
async def test_get_current_price_tick_only_cache_api_failure_propagated(trading_service_fixture, mock_deps):
    """_source='tick' 캐시 우회 후 API 실패 시 오류 응답을 그대로 반환한다."""
    service = trading_service_fixture
    tick_cache = {"output": {"stck_prpr": "3000"}, "_source": "tick"}
    mock_deps.stock_repo.get_current_price.return_value = tick_cache

    error_resp = ResCommonResponse(rt_cd="1", msg1="API 오류", data=None)
    mock_deps.broker.get_current_price.return_value = error_resp

    result = await service.get_current_price("080220")

    mock_deps.broker.get_current_price.assert_awaited_once()
    assert result.rt_cd == "1"
    # API 실패 시 캐시 갱신 없음
    mock_deps.stock_repo.set_current_price.assert_not_called()


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
    
    # 2. 두 번째 호출: 로컬 데이터가 충분함 (600건 이상, 최신일 = yesterday)
    # 과거 데이터 백필 API 호출은 스킵하고, 당일 데이터 API만 호출
    # 최신일이 yesterday(20250101) 이상이어야 stale backfill을 건너뜀
    base_date = datetime(2023, 1, 1)
    rows_600 = [{"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"), "close": 1000.0, "open": 1000.0, "high": 1000.0, "low": 1000.0, "volume": 100} for i in range(604)]
    rows_600.append({"date": "20250101", "close": 1010.0, "open": 1010.0, "high": 1020.0, "low": 1000.0, "volume": 200})  # yesterday
    stock_repo.get_stock_data.return_value = {
        "ohlcv": rows_600,
        "historical_complete": True,
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
    stock_repo.get_stock_data.return_value = {"ohlcv": cached_data, "historical_complete": True}
    
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
    stock_repo.get_stock_data.return_value = {"ohlcv": cached_past_data, "historical_complete": True}
    
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
    from common.types import Exchange
    broker.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)
    stock_repo.set_current_price.assert_called_once_with("005930", api_data)


@pytest.mark.asyncio
async def test_get_current_price_db_fallback_when_market_closed(trading_service_fixture, mock_deps):
    """장 마감 + LRU miss → DB 날짜 == 최근 거래일 → API 호출 없이 반환 및 LRU 적재"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    db_snapshot = {"output": {"stck_prpr": "70000"}, "_source": "daily_snapshot", "_trade_date": "20260318"}
    stock_repo.get_latest_daily_snapshot.return_value = db_snapshot
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False  # 장 마감
    service._mcs.get_latest_trading_date.return_value = "20260318"  # DB 날짜와 일치

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
    from common.types import Exchange
    broker.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)
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
    from common.types import Exchange
    broker.inquire_daily_itemchartprice.assert_awaited_with("005930", start_date="20250101", end_date="20250110", fid_period_div_code="D", exchange=Exchange.KRX)

@pytest.mark.asyncio
async def test_get_asking_price(trading_service_fixture, mock_deps):
    """호가 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_asking_price = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_asking_price("005930")
    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_asking_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)

@pytest.mark.asyncio
async def test_get_time_concluded_prices(trading_service_fixture, mock_deps):
    """시간대별 체결가 조회 위임 테스트"""
    broker = mock_deps.broker
    broker.get_time_concluded_prices = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_time_concluded_prices("005930")
    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_time_concluded_prices.assert_awaited_once_with("005930", exchange=Exchange.KRX)

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
async def test_get_recent_daily_ohlcv_db_first_when_sufficient(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: DB에 충분한 데이터가 있으면 API 호출 없이 DB에서 반환."""
    broker = mock_deps.broker
    service = trading_service_fixture
    db_rows = [{"date": f"2025010{i}", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000} for i in range(1, 6)]

    mock_deps.stock_repo.get_stock_data.return_value = {"ohlcv": db_rows, "historical_complete": True}

    result = await service.get_recent_daily_ohlcv("005930", limit=5)

    assert len(result) == 5
    assert result[0]['date'] == "20250101"
    broker.inquire_daily_itemchartprice.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_no_api_call_during_market_hours_when_db_full(trading_service_fixture, mock_deps):
    """장중(is_market_open=True) + ohlcv_update_task로 DB가 완전히 채워진 상태에서
    get_recent_daily_ohlcv 호출 시 OHLCV API(inquire_daily_itemchartprice)가 절대 호출되지 않아야 한다.
    _analyze_candidate가 실제 사용하는 limit=90 기준으로 검증한다.
    """
    broker = mock_deps.broker
    service = trading_service_fixture

    # ohlcv_update_task가 저장한 600일치 데이터 시뮬레이션
    from datetime import date, timedelta
    base = date(2024, 1, 1)
    db_rows = [
        {"date": (base + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
        for i in range(600)
    ]
    mock_deps.stock_repo.get_stock_data.return_value = {"ohlcv": db_rows, "historical_complete": True}

    # 장중 상태 명시 (fixture에 mcs가 없으므로 직접 주입)
    mcs = MagicMock()
    mcs.is_market_open_now = AsyncMock(return_value=True)
    service._mcs = mcs

    result = await service.get_recent_daily_ohlcv("005930", limit=90)

    assert len(result) == 90
    broker.inquire_daily_itemchartprice.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_db_insufficient_falls_back_to_api(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: DB 데이터가 limit보다 적으면 API fallback."""
    broker = mock_deps.broker
    service = trading_service_fixture
    db_rows = [{"date": "20250101", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}]

    mock_deps.stock_repo.get_stock_data.return_value = {"ohlcv": db_rows, "historical_complete": True}

    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"stck_bsop_date": "20250101", "stck_clpr": "105", "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90", "acml_vol": "1000"}],
    )

    result = await service.get_recent_daily_ohlcv("005930", limit=5)

    broker.inquire_daily_itemchartprice.assert_awaited()
    assert len(result) >= 1
    # API에서 온 데이터가 실제 결과에 포함되어 있는지 검증
    assert any(r['date'] == "20250101" for r in result)


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_end_date_slices_db_data(trading_service_fixture, mock_deps):
    """get_recent_daily_ohlcv: end_date 지정 시 DB-first 경로를 타고 슬라이싱 처리."""
    broker = mock_deps.broker
    service = trading_service_fixture
    
    # DB에 end_date(20241231) 이전 데이터가 충분히 존재하도록 구성
    db_rows = [{"date": f"202412{20+i}", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000} for i in range(12)]
    # 이후 데이터
    db_rows += [{"date": f"2025010{i}", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000} for i in range(1, 6)]

    mock_deps.stock_repo.get_stock_data.return_value = {"ohlcv": db_rows, "historical_complete": True}

    result = await service.get_recent_daily_ohlcv("005930", limit=5, end_date="20241231")

    broker.inquire_daily_itemchartprice.assert_not_awaited()
    mock_deps.stock_repo.get_stock_data.assert_awaited()
    
    assert len(result) == 5
    assert result[-1]["date"] == "20241231"


@pytest.mark.asyncio
async def test_get_price_summary(trading_service_fixture, mock_deps):
    """가격 요약 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_price_summary = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_price_summary("005930")
    
    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_price_summary.assert_awaited_once_with("005930", exchange=Exchange.KRX)
    logger.info.assert_called_with("MarketDataService - 005930 종목 요약 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_info_by_code(trading_service_fixture, mock_deps):
    """종목 상세 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_info_by_code = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_info_by_code("005930")
    
    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_stock_info_by_code.assert_awaited_once_with("005930", exchange=Exchange.KRX)
    logger.info.assert_called_with("MarketDataService - 005930 종목 상세 정보 조회 요청")

@pytest.mark.asyncio
async def test_get_stock_conclusion(trading_service_fixture, mock_deps):
    """체결 정보 조회 위임 테스트"""
    broker = mock_deps.broker
    logger = mock_deps.logger
    broker.get_stock_conclusion = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data={}))
    
    resp = await trading_service_fixture.get_stock_conclusion("005930")
    
    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_stock_conclusion.assert_awaited_once_with("005930", exchange=Exchange.KRX)
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
    """일별 분봉 조회는 모의투자에서도 조회 엔드포인트로 위임한다."""
    broker = mock_deps.broker
    env = mock_deps.env
    env.is_paper_trading = True
    expected = ResCommonResponse(rt_cd="0", msg1="OK", data={})
    broker.inquire_time_dailychartprice = AsyncMock(return_value=expected)

    resp = await trading_service_fixture.get_intraday_minutes_by_date(stock_code="005930", input_date_1="20250101")

    assert resp == expected
    broker.inquire_time_dailychartprice.assert_awaited_once_with(
        stock_code="005930", input_date_1="20250101", input_hour_1="", pw_data_incu_yn="Y", fake_tick_incu_yn=""
    )

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
    from common.types import Exchange
    broker.inquire_daily_itemchartprice.assert_awaited_once_with(
        stock_code="005930", start_date="20250101", end_date="20250110", fid_period_div_code="D", exchange=Exchange.KRX
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
    """장 마감 후에는 현재가 API(get_current_price) 대신 일봉 API로 오늘 최종 캔들을 취득한다.

    장 마감 후 DB = yesterday인 경우:
    - get_current_price(실시간 틱)는 호출하지 않음
    - inquire_daily_itemchartprice(일봉)로 오늘 확정 캔들을 backfill
    - 최종 데이터에 오늘(20250102) 포함
    """
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    # 장 마감 후 (18:00), today=0102, yesterday=0101
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 18, 0, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False

    # DB: 어제(0101)까지 데이터 존재
    base_date = datetime(2023, 1, 1)
    past_rows = [
        {"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 100}
        for i in range(600)
    ]
    past_rows.append({"date": "20250101", "open": 1010.0, "high": 1020.0, "low": 1000.0, "close": 1015.0, "volume": 200})
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows, "historical_complete": True}

    # 일봉 API: 오늘(0102) 확정 캔들 반환
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[
            {"stck_bsop_date": "20250101", "stck_oprc": "1010", "stck_hgpr": "1020", "stck_lwpr": "1000", "stck_clpr": "1015", "acml_vol": "200"},
            {"stck_bsop_date": "20250102", "stck_oprc": "1015", "stck_hgpr": "1025", "stck_lwpr": "1005", "stck_clpr": "1020", "acml_vol": "300"},
        ]
    )

    resp = await service.get_ohlcv("005930", period="D")

    assert resp.rt_cd == "0"
    # 현재가(실시간 틱) API는 호출하지 않음 (장 마감 후)
    broker.get_current_price.assert_not_called()
    # 일봉 API로 오늘 확정 캔들 backfill
    broker.inquire_daily_itemchartprice.assert_called()
    # 오늘(0102) 최종 캔들 포함
    assert resp.data[-1]['date'] == "20250102"
    assert resp.data[-1]['close'] == 1020.0


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
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows, "historical_complete": True}

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


@pytest.mark.asyncio
async def test_get_ohlcv_after_market_close_with_stale_db_backfills(trading_service_fixture, mock_deps):
    """장 마감 + DB 데이터가 수일 전까지만 있을 때(yesterday보다 오래됨) 과거 일봉 API 백필 수행 검증.

    버그 재현:
      - DB에 0320까지 600일치 데이터 있음 (historical_complete=True)
      - 오늘은 0326 18:00 (장 마감 후)
      - yesterday = 0325, DB 최신일 0320 < yesterday → 5개 거래일 누락
      - 기존 코드: historical_complete=True → backfill 스킵,
                  is_market_open=False → 오늘 fetch 스킵 → 누락 데이터 그대로 반환
      - 수정 후: DB 최신일 < yesterday이면 API backfill 호출해야 함
    """
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    # 장 마감 후 (0326 18:00), yesterday = 0325
    tm.get_current_kst_time.return_value = datetime(2025, 3, 26, 18, 0, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False

    # DB에 0320까지만 데이터 존재 (historical_complete=True)
    base_date = datetime(2023, 1, 1)
    past_rows = [
        {"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 100}
        for i in range(599)
    ]
    past_rows.append({"date": "20250320", "open": 1010.0, "high": 1020.0, "low": 1000.0, "close": 1015.0, "volume": 200})
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows, "historical_complete": True}

    # API: 0320-0325 누락 구간 포함 데이터 반환
    new_rows = [
        {"stck_bsop_date": "20250320", "stck_oprc": "1010", "stck_hgpr": "1020", "stck_lwpr": "1000", "stck_clpr": "1015", "acml_vol": "200"},
        {"stck_bsop_date": "20250321", "stck_oprc": "1020", "stck_hgpr": "1030", "stck_lwpr": "1010", "stck_clpr": "1025", "acml_vol": "300"},
        {"stck_bsop_date": "20250324", "stck_oprc": "1025", "stck_hgpr": "1035", "stck_lwpr": "1015", "stck_clpr": "1030", "acml_vol": "250"},
        {"stck_bsop_date": "20250325", "stck_oprc": "1030", "stck_hgpr": "1040", "stck_lwpr": "1020", "stck_clpr": "1035", "acml_vol": "280"},
    ]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=new_rows)

    resp = await service.get_ohlcv("005930", period="D")

    assert resp.rt_cd == "0"
    # 장 마감 후라도 DB 데이터가 오래됐으면 일봉 API를 호출해야 함
    broker.inquire_daily_itemchartprice.assert_called()
    # 최종 데이터에 0325(어제) 포함되어야 함
    dates = [r['date'] for r in resp.data]
    assert "20250325" in dates, f"어제(0325) 데이터가 누락됨: {dates[-5:]}"
    # DB의 전체 600일치도 포함되어야 함
    assert len(resp.data) >= 600


@pytest.mark.asyncio
async def test_get_ohlcv_after_market_close_fetches_today_when_yesterday_is_latest(trading_service_fixture, mock_deps):
    """장 마감 후 DB 최신일 = yesterday일 때도 오늘 최종 캔들 backfill 수행 검증.

    버그 재현:
      - 오늘 0326 23:21 (장 마감 후), yesterday = 0325
      - DB: 0325(어제)까지 존재, historical_complete=True
      - 기존 코드: latest_in_db(0325) < yesterday_str(0325) → False → backfill 스킵
                  → 오늘(0326) 최종 캔들 누락 반환
      - 수정 후: 장 마감이면 fetch_end_date = today_str(0326)
                latest_in_db(0325) < fetch_end_date(0326) → True → backfill 수행
                → 오늘(0326) 최종 캔들 포함 반환
    """
    broker = mock_deps.broker
    tm = mock_deps.tm
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    # 장 마감 후 (0326 23:21), yesterday = 0325
    tm.get_current_kst_time.return_value = datetime(2025, 3, 26, 23, 21, 0)
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False

    # DB: 어제(0325)까지 데이터 존재, historical_complete=True
    base_date = datetime(2023, 1, 1)
    past_rows = [
        {"date": (base_date + timedelta(days=i)).strftime("%Y%m%d"),
         "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 100}
        for i in range(599)
    ]
    past_rows.append({"date": "20250325", "open": 1010.0, "high": 1020.0, "low": 1000.0, "close": 1015.0, "volume": 200})
    stock_repo.get_stock_data.return_value = {"ohlcv": past_rows, "historical_complete": True}

    # API: 오늘(0326) 최종 캔들 포함해서 반환
    api_rows = [
        {"stck_bsop_date": "20250325", "stck_oprc": "1010", "stck_hgpr": "1020", "stck_lwpr": "1000", "stck_clpr": "1015", "acml_vol": "200"},
        {"stck_bsop_date": "20250326", "stck_oprc": "1015", "stck_hgpr": "1025", "stck_lwpr": "1005", "stck_clpr": "1020", "acml_vol": "350"},
    ]
    broker.inquire_daily_itemchartprice.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_rows)

    resp = await service.get_ohlcv("005930", period="D")

    assert resp.rt_cd == "0"
    # 장 마감 후 latest_in_db = yesterday여도 오늘치 backfill API를 호출해야 함
    broker.inquire_daily_itemchartprice.assert_called()
    dates = [r['date'] for r in resp.data]
    # 오늘(0326) 최종 캔들이 포함되어야 함
    assert "20250326" in dates, f"오늘(0326) 데이터가 누락됨: {dates[-5:]}"
    assert resp.data[-1]['date'] == "20250326"
    assert resp.data[-1]['close'] == 1020.0
    # DB의 600일치도 유지되어야 함
    assert len(resp.data) >= 600


# ── get_current_price DB 날짜 검증 시나리오 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_price_db_stale_date_falls_back_to_api(trading_service_fixture, mock_deps):
    """장 마감 + DB 날짜 != 최근 거래일 → 오래된 데이터이므로 API 호출로 폴백"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    db_snapshot = {"output": {"stck_prpr": "70000"}, "_source": "daily_snapshot", "_trade_date": "20260317"}
    stock_repo.get_latest_daily_snapshot.return_value = db_snapshot
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False
    service._mcs.get_latest_trading_date.return_value = "20260318"  # DB(0317) != 최근 거래일(0318)

    api_data = {"output": {"stck_prpr": "71000"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)
    stock_repo.set_current_price.assert_called_once_with("005930", api_data)


@pytest.mark.asyncio
async def test_get_current_price_db_latest_trading_date_none_falls_back_to_api(trading_service_fixture, mock_deps):
    """장 마감 + get_latest_trading_date() == None → 날짜 검증 불가 → API 호출"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    db_snapshot = {"output": {"stck_prpr": "70000"}, "_source": "daily_snapshot", "_trade_date": "20260318"}
    stock_repo.get_latest_daily_snapshot.return_value = db_snapshot
    service._mcs = AsyncMock()
    service._mcs.is_market_open_now.return_value = False
    service._mcs.get_latest_trading_date.return_value = None  # 최근 거래일 조회 실패

    api_data = {"output": {"stck_prpr": "71000"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_get_current_price_no_mcs_falls_back_to_api(trading_service_fixture, mock_deps):
    """_mcs 없이 장 마감 판정 + DB에 데이터 있어도 → 날짜 검증 불가 → API 호출"""
    broker = mock_deps.broker
    stock_repo = mock_deps.stock_repo
    service = trading_service_fixture

    stock_repo.get_current_price.return_value = None
    db_snapshot = {"output": {"stck_prpr": "70000"}, "_source": "daily_snapshot", "_trade_date": "20260318"}
    stock_repo.get_latest_daily_snapshot.return_value = db_snapshot
    service._mcs = None
    service._market_clock = MagicMock()
    service._market_clock.is_market_operating_hours.return_value = False  # 장 마감

    api_data = {"output": {"stck_prpr": "71000"}}
    broker.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=api_data)

    resp = await service.get_current_price("005930")

    assert resp.rt_cd == "0"
    from common.types import Exchange
    broker.get_current_price.assert_awaited_once_with("005930", exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_get_top_market_cap_stocks_code_uses_default_limit_when_missing(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    mock_deps.env.is_paper_trading = False
    expected = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    mock_deps.broker.get_top_market_cap_stocks_code = AsyncMock(return_value=expected)

    result = await service.get_top_market_cap_stocks_code("0000")

    assert result == expected
    mock_deps.broker.get_top_market_cap_stocks_code.assert_awaited_once_with("0000", 30)
    mock_deps.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_current_upper_limit_stocks_accepts_dict_items(trading_service_fixture, mock_deps):
    result = await trading_service_fixture.get_current_upper_limit_stocks([
        {
            "stck_shrn_iscd": "005930",
            "stck_prpr": "70000",
            "prdy_ctrt": "29.5",
            "hts_kor_isnm": "삼성전자",
            "prdy_vrss": "1500",
        }
    ])

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == []
    mock_deps.logger.warning.assert_called_once()
    mock_deps.tm.get_current_kst_time.assert_called_once()


@pytest.mark.asyncio
async def test_get_top_trading_value_stocks_handles_invalid_amount_and_blank_code(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    mock_deps.broker.get_top_volume_stocks = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "acml_tr_pbmn": "bad"},
            {"mksc_shrn_iscd": "", "hts_kor_isnm": "이름만있음", "acml_tr_pbmn": "100"},
        ]},
    ))
    mock_deps.broker.get_top_market_cap_stocks_code = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
    ])
    mock_deps.broker.get_top_rise_fall_stocks = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
        ResCommonResponse(rt_cd="0", msg1="OK", data=[]),
    ])

    result = await service.get_top_trading_value_stocks()

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 1
    assert result.data[0]["mksc_shrn_iscd"] == "005930"
    assert result.data[0]["acml_tr_pbmn"] == "bad"


def test_wrap_snapshot_output_returns_original_when_output_is_not_dict(trading_service_fixture):
    db_data = {"output": ["unexpected"]}

    wrapped = trading_service_fixture._wrap_snapshot_output(db_data)

    assert wrapped == db_data


def test_wrap_snapshot_output_builds_model_with_defaults(trading_service_fixture):
    db_data = {"output": {"stck_prpr": "70000", "stck_bsop_date": "20250102"}}

    wrapped = trading_service_fixture._wrap_snapshot_output(db_data)

    assert isinstance(wrapped["output"], ResStockFullInfoApiOutput)
    assert wrapped["output"].stck_prpr == "70000"
    assert wrapped["output"].new_hgpr_lwpr_cls_code is None


def test_normalize_ohlcv_rows_handles_objects_none_and_sorts(trading_service_fixture):
    rows = trading_service_fixture._normalize_ohlcv_rows([
        None,
        {"date": "20250103", "open": "1,000", "high": "1,100", "low": "900", "close": "1,050", "volume": "1,234"},
        SimpleNamespace(
            stck_bsop_date="20250102",
            stck_oprc="950",
            stck_hgpr="980",
            stck_lwpr="930",
            stck_clpr="970",
            acml_vol="321",
        ),
        {"close": "1000"},
    ])

    assert [row["date"] for row in rows] == ["20250102", "20250103"]
    assert rows[0]["close"] == 970.0
    assert rows[1]["volume"] == 1234


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_returns_db_slice_when_sufficient(trading_service_fixture, mock_deps):
    rows = [
        {"date": f"202501{idx:02d}", "open": 1.0, "high": 1.0, "low": 1.0, "close": float(idx), "volume": idx}
        for idx in range(1, 6)
    ]
    mock_deps.stock_repo.get_stock_data.return_value = {"ohlcv": rows}

    result = await trading_service_fixture.get_recent_daily_ohlcv("005930", limit=3)

    assert [row["date"] for row in result] == ["20250103", "20250104", "20250105"]
    mock_deps.stock_repo.upsert_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_with_start_date_uses_range_api(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    service.get_ohlcv_range = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data=[{"date": "20250102", "close": 10.0}],
    ))

    result = await service.get_recent_daily_ohlcv("005930", start_date="20250101", end_date="20250131")

    assert result == [{"date": "20250102", "close": 10.0}]
    from common.types import Exchange
    service.get_ohlcv_range.assert_awaited_once_with("005930", "D", "20250101", "20250131", exchange=Exchange.KRX)


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_merges_responses_and_trims_limit(trading_service_fixture, mock_deps):
    service = trading_service_fixture
    service.get_ohlcv_range = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[
        {"date": "20250101", "close": 1.0},
        {"date": "20250102", "close": 20.0},
        {"date": "20250103", "close": 3.0},
    ]))

    result = await service.get_recent_daily_ohlcv("005930", limit=2, end_date="20250131")

    assert [row["date"] for row in result] == ["20250102", "20250103"]
    mock_deps.stock_repo.upsert_ohlcv.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_recent_daily_ohlcv_ignores_failed_range_response(trading_service_fixture):
    service = trading_service_fixture
    service._stock_repo = None
    service.get_ohlcv_range = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd="0", msg1="OK", data=[
            {"date": "20250101", "close": 1.0},
            {"date": "20250102", "close": 2.0},
        ]),
        Exception("temporary"),
    ])

    result = await service.get_recent_daily_ohlcv("005930", limit=100, end_date="20250131")

    assert [row["date"] for row in result] == ["20250101", "20250102"]


@pytest.mark.asyncio
async def test_get_intraday_minutes_today_delegates_to_broker(trading_service_fixture, mock_deps):
    expected = ResCommonResponse(rt_cd="0", msg1="OK", data=[{"x": 1}])
    mock_deps.broker.inquire_time_itemchartprice = AsyncMock(return_value=expected)

    result = await trading_service_fixture.get_intraday_minutes_today(stock_code="005930", input_hour_1="093000")

    assert result == expected
    mock_deps.broker.inquire_time_itemchartprice.assert_awaited_once_with(
        stock_code="005930",
        input_hour_1="093000",
        pw_data_incu_yn="Y",
        etc_cls_code="0",
    )


@pytest.mark.asyncio
async def test_get_intraday_minutes_by_date_delegates_in_paper_mode(trading_service_fixture, mock_deps):
    mock_deps.env.is_paper_trading = True
    expected = ResCommonResponse(rt_cd="0", msg1="OK", data=[{"date": "20250102"}])
    mock_deps.broker.inquire_time_dailychartprice = AsyncMock(return_value=expected)

    result = await trading_service_fixture.get_intraday_minutes_by_date(stock_code="005930", input_date_1="20250102")

    assert result == expected
    mock_deps.broker.inquire_time_dailychartprice.assert_awaited_once_with(
        stock_code="005930",
        input_date_1="20250102",
        input_hour_1="",
        pw_data_incu_yn="Y",
        fake_tick_incu_yn="",
    )


@pytest.mark.asyncio
async def test_get_intraday_minutes_by_date_delegates_in_real_mode(trading_service_fixture, mock_deps):
    mock_deps.env.is_paper_trading = False
    expected = ResCommonResponse(rt_cd="0", msg1="OK", data=[{"date": "20250102"}])
    mock_deps.broker.inquire_time_dailychartprice = AsyncMock(return_value=expected)

    result = await trading_service_fixture.get_intraday_minutes_by_date(
        stock_code="005930",
        input_date_1="20250102",
        input_hour_1="100000",
    )

    assert result == expected
    mock_deps.broker.inquire_time_dailychartprice.assert_awaited_once_with(
        stock_code="005930",
        input_date_1="20250102",
        input_hour_1="100000",
        pw_data_incu_yn="Y",
        fake_tick_incu_yn="",
    )


@pytest.mark.asyncio
async def test_get_latest_trading_date_returns_none_without_calendar_service(trading_service_fixture):
    trading_service_fixture._mcs = None

    assert await trading_service_fixture.get_latest_trading_date() is None


@pytest.mark.asyncio
async def test_get_next_open_day_skips_failures_and_closed_days(trading_service_fixture, mock_deps):
    mock_deps.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    mock_deps.broker.check_holiday = AsyncMock(side_effect=[
        None,
        ResCommonResponse(rt_cd="1", msg1="fail", data={}),
        ResCommonResponse(rt_cd="0", msg1="OK", data={"output": [
            {"bass_dt": "20250103", "bzdy_yn": "N"},
            {"bass_dt": "20250104", "bzdy_yn": "Y"},
        ]}),
    ])

    result = await trading_service_fixture.get_next_open_day()

    assert result == "20250104"


@pytest.mark.asyncio
async def test_get_next_open_day_returns_none_when_no_open_day_found(trading_service_fixture, mock_deps):
    mock_deps.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    mock_deps.broker.check_holiday = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": []},
    ))

    result = await trading_service_fixture.get_next_open_day()

    assert result is None


@pytest.mark.asyncio
async def test_get_next_open_day_advances_when_batch_has_no_open_day(trading_service_fixture, mock_deps):
    mock_deps.tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    mock_deps.broker.check_holiday = AsyncMock(side_effect=[
        ResCommonResponse(
            rt_cd="0",
            msg1="OK",
            data={"output": [
                {"bass_dt": "20250101", "bzdy_yn": "N"},
                {"bass_dt": "20250102", "bzdy_yn": "N"},
            ]},
        ),
        ResCommonResponse(
            rt_cd="0",
            msg1="OK",
            data={"output": [
                {"bass_dt": "20250103", "bzdy_yn": "Y"},
            ]},
        ),
    ])

    result = await trading_service_fixture.get_next_open_day()

    assert result == "20250103"
