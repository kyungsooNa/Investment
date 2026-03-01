# tests/unit_test/test_oneil_universe_service.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
import pandas as pd

from common.types import ResCommonResponse, ErrorCode
from common.types import ResStockFullInfoApiOutput, ResBollingerBand, ResRelativeStrength
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OSBWatchlistItem

def create_mock_ohlcv(length=90, zero_volume_days=0, no_high_days=0):
    """테스트 목적에 맞는 OHLCV 목 데이터를 생성합니다."""
    data = []
    for i in range(length):
        day_index = length - 1 - i
        is_zero_vol = i < zero_volume_days
        is_no_high = i < no_high_days

        data.append({
            "date": f"202312{31-day_index:02d}",
            "open": 10000 + i * 10,
            "high": None if is_no_high else 10100 + i * 10,
            "low": 9900 + i * 10,
            "close": 10050 + i * 10,
            "volume": 0 if is_zero_vol else 10000 + i * 100
        })
    return data


@pytest.fixture
def mock_deps():
    ts = MagicMock()
    sqs = MagicMock()
    indicator = MagicMock()
    mapper = MagicMock()
    tm = MagicMock()
    logger = MagicMock()
    
    # 공통 Mock 설정
    ts.get_current_stock_price = AsyncMock()
    ts.get_recent_daily_ohlcv = AsyncMock()
    ts.get_financial_ratio = AsyncMock()
    indicator.get_bollinger_bands = AsyncMock()
    indicator.get_relative_strength = AsyncMock()
    
    return ts, sqs, indicator, mapper, tm, logger

@pytest.fixture
def oneil_service_fixture():
    """OneilUniverseService와 Mock 종속성을 제공하는 픽스처입니다."""
    mock_ts = AsyncMock()
    mock_sqs = AsyncMock()
    mock_indicator = AsyncMock()
    mock_mapper = MagicMock()
    mock_tm = MagicMock()
    mock_logger = MagicMock()

    service = OneilUniverseService(
        trading_service=mock_ts,
        stock_query_service=mock_sqs,
        indicator_service=mock_indicator,
        stock_code_mapper=mock_mapper,
        time_manager=mock_tm,
        logger=mock_logger
    )
    # 공통 Mock 응답 설정
    mock_price_output = ResStockFullInfoApiOutput(w52_hgpr="12000", hts_avls="1000")
    mock_ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, data={"output": mock_price_output}
    )
    mock_indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        data=[ResBollingerBand(code="TEST", date="20231231", close=1, upper=1, lower=1, middle=1)] * 90
    )
    mock_indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        data=ResRelativeStrength(code="TEST", date="20231231", return_pct=10.0)
    )
    return service, mock_ts, mock_indicator

@pytest.mark.asyncio
async def test_analyze_candidate_success(mock_deps):
    """_analyze_candidate: 모든 필터 조건을 통과하는 경우 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (정배열 조건: Close > MA20 > MA50)
    # 최근 50일 데이터 생성
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 10000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 2. 현재가 Mock (52주 고가 대비 20% 이내)
    # 현재가(prev_close)는 약 1099. 52주 고가 1200이면 통과.
    # 시가총액 1000억으로 가정 (hts_avls는 억 단위)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
    )
    
    # 3. BB Mock (스퀴즈 데이터 계산용)
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", data=[MagicMock(upper=110, lower=90) for _ in range(30)]
    )
    
    # 4. RS Mock
    indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd="0", data=MagicMock(return_pct=10.0)
    )
    
    mapper.is_kosdaq.return_value = True
    
    # 실행
    item = await service._analyze_candidate("005930", "Samsung")
    
    # 검증
    assert item is not None
    assert isinstance(item, OSBWatchlistItem)
    assert item.code == "005930"
    assert item.market == "KOSDAQ"
    assert item.rs_return_3m == 10.0

@pytest.mark.asyncio
async def test_analyze_candidate_filter_trading_value(mock_deps):
    """_analyze_candidate: 거래대금 부족 시 None 반환 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 거래량/가격이 매우 낮음 -> 거래대금 100억 미만
    ohlcv = [{"close": 100, "high": 110, "volume": 10} for _ in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    item = await service._analyze_candidate("005930", "Samsung")
    assert item is None

@pytest.mark.asyncio
async def test_generate_pool_a(mock_deps):
    """generate_pool_a: 전체 종목 스캔 및 파일 저장 로직 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. 전체 종목 리스트 Mock
    mapper.df = pd.DataFrame({
        "종목코드": ["000001", "000002"],
        "종목명": ["StockA", "StockB"],
        "시장구분": ["KOSPI", "KOSDAQ"]
    })
    
    # 2. 1차 필터 (시총/거래대금) Mock
    # StockA: 통과, StockB: 탈락 (거래대금 부족)
    async def mock_get_price(code):
        if code == "000001":
            # 시가총액 5000억, 거래대금 200억 (hts_avls는 억 단위)
            return ResCommonResponse(rt_cd="0", data={"output": {"hts_avls": "5000", "acml_tr_pbmn": "20000000000"}})
        # 시가총액 10억, 거래대금 100원
        return ResCommonResponse(rt_cd="0", data={"output": {"hts_avls": "10", "acml_tr_pbmn": "100"}})
    
    ts.get_current_stock_price.side_effect = mock_get_price
    
    # 3. 2차 필터 (_analyze_candidate) Mock
    # StockA 통과 가정
    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = OSBWatchlistItem(
            code="000001", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=20000000000,
            market_cap=500000000000
        )
        
        # 4. 파일 저장 Mock
        with patch.object(service, '_save_pool_a') as mock_save:
            result = await service.generate_pool_a()
            
            assert result['total_scanned'] == 2
            assert result['passed_first'] == 1  # StockA만 통과
            mock_save.assert_called_once()

@pytest.mark.asyncio
async def test_get_watchlist_refresh_logic(mock_deps):
    """get_watchlist: 시간 경과에 따른 갱신 및 캐싱 로직 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 시간 설정: 장 시작 후 10분 (첫 갱신 시점)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 10, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    
    # 내부 메서드 Mock
    with patch.object(service, '_load_pool_a', return_value=[]), \
         patch.object(service, '_build_pool_b', return_value={}):
        
        # 1. 첫 호출: 워치리스트 빌드 수행
        await service.get_watchlist()
        assert service._watchlist_date == "20250101"
        assert 10 in service._watchlist_refresh_done
        
        # 2. 같은 시간 재호출: 캐시 사용 (빌드 수행 안 함)
        service._watchlist_date = "20250101" # 상태 유지 가정
        with patch.object(service, '_build_watchlist') as mock_build:
            await service.get_watchlist()
            mock_build.assert_not_called()
            
        # 3. 시간 경과 (30분): 갱신 수행
        tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 30, 0)
        with patch.object(service, '_build_watchlist') as mock_build:
            await service.get_watchlist()
            mock_build.assert_awaited_once()
            assert 30 in service._watchlist_refresh_done

@pytest.mark.asyncio
async def test_analyze_candidate_with_zero_volume(oneil_service_fixture):
    """
    _analyze_candidate가 최근 거래량이 모두 0인 종목을 처리할 때 ZeroDivisionError 없이 None을 반환하는지 테스트합니다.
    """
    # Arrange
    service, mock_ts, _ = oneil_service_fixture
    # 최근 20일간의 거래량이 모두 0인 OHLCV 데이터를 모킹합니다.
    mock_ohlcv = create_mock_ohlcv(length=90, zero_volume_days=20)
    mock_ts.get_recent_daily_ohlcv.return_value = mock_ohlcv

    # Act
    result = await service._analyze_candidate("TESTCODE", "Test Stock")

    # Assert
    # 함수는 오류를 발생시키지 않고 None을 반환해야 합니다.
    assert result is None
    mock_ts.get_recent_daily_ohlcv.assert_awaited_once_with("TESTCODE", limit=90)

@pytest.mark.asyncio
async def test_analyze_candidate_with_no_high_data(oneil_service_fixture):
    """
    _analyze_candidate가 최근 고가(high) 데이터가 없는 경우 None을 반환하는지 테스트합니다.
    """
    # Arrange
    service, mock_ts, _ = oneil_service_fixture
    mock_ohlcv = create_mock_ohlcv(length=90, no_high_days=20)
    mock_ts.get_recent_daily_ohlcv.return_value = mock_ohlcv

    # Act
    result = await service._analyze_candidate("TESTCODE", "Test Stock")

    # Assert
    assert result is None
    mock_ts.get_recent_daily_ohlcv.assert_awaited_once_with("TESTCODE", limit=90)
    # 조기 반환되므로 추가 API 호출이 없어야 합니다.
    mock_ts.get_current_stock_price.assert_not_called()
