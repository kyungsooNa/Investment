# tests/unit_test/strategies/oneil/test_universe_service.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
import pandas as pd

from common.types import ResCommonResponse, ErrorCode
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OSBWatchlistItem

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

@pytest.mark.asyncio
async def test_analyze_candidate_success(mock_deps):
    """_analyze_candidate: 모든 필터 조건을 통과하는 경우 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (정배열 조건: Close > MA20 > MA50)
    # 최근 50일 데이터 생성
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 15000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 2. 현재가 Mock (52주 고가 대비 20% 이내)
    # 현재가(prev_close)는 약 1099. 52주 고가 1200이면 통과.
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "stck_llam": "100000000000"}}
    )
    
    # 3. BB Mock (스퀴즈 데이터 계산용)
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[MagicMock(upper=110, lower=90) for _ in range(30)]
    )
    
    # 4. RS Mock
    indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=MagicMock(return_pct=10.0)
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
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_llam": "500000000000", "acml_tr_pbmn": "20000000000"}})
        return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_llam": "10000000", "acml_tr_pbmn": "100"}})
    
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