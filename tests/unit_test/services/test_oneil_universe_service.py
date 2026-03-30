# tests/unit_test/test_oneil_universe_service.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, mock_open, call
from datetime import datetime
import pandas as pd
import json

from common.types import ResCommonResponse, ErrorCode
from common.types import ResStockFullInfoApiOutput, ResBollingerBand, ResRelativeStrength
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OSBWatchlistItem
from core.logger import get_strategy_logger
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from repositories.stock_code_repository import StockCodeRepository
from core.market_clock import MarketClock

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


def create_mock_stock_info(overrides=None):
    base_data = {name: "0" for name in ResStockFullInfoApiOutput.model_fields}
    if overrides:
        base_data.update(overrides)
    return ResStockFullInfoApiOutput.model_validate(base_data)

@pytest.fixture
def mock_deps():
    # ts = MagicMock() # Removed
    sqs = MagicMock(spec=StockQueryService)
    indicator = MagicMock(spec=IndicatorService)
    mapper = MagicMock(spec=StockCodeRepository)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()
    
    # 공통 Mock 설정 (SQS로 이동)
    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    sqs.get_recent_daily_ohlcv = AsyncMock(spec=StockQueryService.get_recent_daily_ohlcv)
    sqs.get_financial_ratio = AsyncMock(spec=StockQueryService.get_financial_ratio)
    sqs.get_top_trading_value_stocks = AsyncMock(spec=StockQueryService.get_top_trading_value_stocks)
    sqs.get_top_rise_fall_stocks = AsyncMock(spec=StockQueryService.get_top_rise_fall_stocks)
    sqs.get_top_volume_stocks = AsyncMock(spec=StockQueryService.get_top_volume_stocks)
    indicator.calc_bb_widths_sync = MagicMock(return_value=[20.0] * 30)
    indicator.calc_rs_sync = MagicMock(return_value=10.0)
    
    return None, sqs, indicator, mapper, tm, logger # ts is None

@pytest.fixture
def oneil_service_fixture():
    """OneilUniverseService와 Mock 종속성을 제공하는 픽스처입니다."""
    # mock_ts = AsyncMock() # Removed
    mock_sqs = AsyncMock(spec=StockQueryService)
    mock_indicator = AsyncMock(spec=IndicatorService)
    mock_mapper = MagicMock(spec=StockCodeRepository)
    mock_tm = MagicMock(spec=MarketClock)
    mock_logger = MagicMock()

    service = OneilUniverseService(
        stock_query_service=mock_sqs,
        indicator_service=mock_indicator,
        stock_code_repository=mock_mapper,
        market_clock=mock_tm,
        logger=mock_logger
    )
    # 공통 Mock 응답 설정
    mock_price_output = create_mock_stock_info({"w52_hgpr": "12000", "hts_avls": "1000"})
    mock_sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": mock_price_output}
    )
    mock_indicator.calc_bb_widths_sync = MagicMock(return_value=[20.0] * 30)
    mock_indicator.calc_rs_sync = MagicMock(return_value=10.0)
    return service, mock_sqs, mock_indicator

async def test_analyze_candidate_success(mock_deps):
    """_analyze_candidate: 모든 필터 조건을 통과하는 경우 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (정배열 조건: Close > MA20 > MA50)
    # 최근 50일 데이터 생성
    # 거래대금 조건(100억)을 만족하기 위해 volume을 충분히 크게 설정 (1000원 * 15,000,000주 = 150억)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # 2. 현재가 Mock (52주 고가 대비 20% 이내)
    # 현재가(prev_close)는 약 1099. 52주 고가 1200이면 통과.
    # 시가총액 3000억으로 가정 (hts_avls는 억 단위, 2000억~2조 범위 내)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "3000"})}
    )
    
    # 3. BB Mock (스퀴즈 데이터 계산용)
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90

    # 4. RS Mock
    indicator.calc_rs_sync.return_value = 10.0
    
    mapper.is_kosdaq.return_value = True
    
    # 실행
    item = await service._analyze_candidate("005930", "Samsung")
    
    # 검증
    assert item is not None
    assert isinstance(item, OSBWatchlistItem)
    assert item.code == "005930"
    assert item.market == "KOSDAQ"
    assert item.rs_return_3m == 10.0

async def test_analyze_candidate_filter_market_cap(mock_deps):
    """_analyze_candidate: 시가총액 범위(2천억~2조) 벗어날 시 탈락 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (기본 통과 조건)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # Case 1: 시가총액 미달 (1000억)
    # hts_avls는 억 단위. 1000억 -> "1000"
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "1000"})}
    )
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is None
    
    # Case 2: 시가총액 초과 (21조 = 210000억, cap_max=20조 초과)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "210000"})}
    )
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is None

    # Case 3: 정상 범위 (5000억)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "5000"})}
    )
    # BB, RS Mock 필요 (통과를 위해)
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90
    indicator.calc_rs_sync.return_value = 10.0
    
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is not None

async def test_analyze_candidate_filter_trading_value(mock_deps):
    """_analyze_candidate: 거래대금 부족 시 None 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 거래량/가격이 매우 낮음 -> 거래대금 100억 미만
    ohlcv = [{"close": 100, "high": 110, "volume": 10} for _ in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    item = await service._analyze_candidate("005930", "Samsung")
    assert item is None

async def test_generate_premium_watchlist(mock_deps, tmp_path):
    """generate_premium_watchlist: 전체 종목 스캔 및 파일 저장 로직 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)

    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    # 1. 전체 종목 리스트 Mock
    mapper.df = pd.DataFrame({
        "종목코드": ["000001", "000002"],
        "종목명": ["StockA", "StockB"],
        "시장구분": ["KOSPI", "KOSDAQ"]
    })
    
    # 2. 1차 필터 (시총) Mock
    # StockA: 통과, StockB: 탈락 (시가총액 미달)
    async def mock_get_price(code, **kwargs):
        if code == "000001":
            # 시가총액 5000억 (hts_avls는 억 단위)
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"hts_avls": "5000"})})
        # 시가총액 10억 (미달)
        return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"hts_avls": "10"})})
    
    sqs.get_current_price.side_effect = mock_get_price
    
    # Redirect logs to tmp_path
    def mock_get_logger_side_effect(name, sub_dir=None):
        return get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    # 3. 2차 필터 (_analyze_candidate) Mock
    # StockA 통과 가정
    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect):
        mock_analyze.return_value = OSBWatchlistItem(
            code="000001", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=20000000000,
            market_cap=500000000000
        )
        
        # 4. 파일 저장 Mock
        with patch.object(service, '_save_premium_stocks') as mock_save:
            result = await service.generate_premium_watchlist()
            
            assert result['total_scanned'] == 2
            assert result['passed_first'] == 1  # StockA만 통과
            mock_save.assert_called_once()

async def test_get_watchlist_refresh_logic(mock_deps):
    """get_watchlist: 시간 경과에 따른 갱신 및 캐싱 로직 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 시간 설정: 장 시작 후 10분 (첫 갱신 시점)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 10, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    
    # 내부 메서드 Mock
    with patch.object(service, '_load_premium_stocks', return_value=[]), \
         patch.object(service, '_build_daily_surge_pool', return_value={}):
        
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

async def test_analyze_candidate_with_zero_volume(oneil_service_fixture):
    """
    _analyze_candidate가 최근 거래량이 모두 0인 종목을 처리할 때 ZeroDivisionError 없이 None을 반환하는지 테스트합니다.
    """
    # Arrange
    service, mock_sqs, _ = oneil_service_fixture
    # 최근 20일간의 거래량이 모두 0인 OHLCV 데이터를 모킹합니다.
    mock_ohlcv = create_mock_ohlcv(length=90, zero_volume_days=20)
    mock_sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=mock_ohlcv)

    # Act
    result = await service._analyze_candidate("TESTCODE", "Test Stock")

    # Assert
    # 함수는 오류를 발생시키지 않고 None을 반환해야 합니다.
    assert result is None
    mock_sqs.get_recent_daily_ohlcv.assert_awaited_once_with("TESTCODE", limit=90)

async def test_analyze_candidate_with_no_high_data(oneil_service_fixture):
    """
    _analyze_candidate가 최근 고가(high) 데이터가 없는 경우 None을 반환하는지 테스트합니다.
    """
    # Arrange
    service, mock_sqs, _ = oneil_service_fixture
    mock_ohlcv = create_mock_ohlcv(length=90, no_high_days=20)
    mock_sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=mock_ohlcv)

    # Act
    result = await service._analyze_candidate("TESTCODE", "Test Stock")

    # Assert
    assert result is None
    mock_sqs.get_recent_daily_ohlcv.assert_awaited_once_with("TESTCODE", limit=90)
    # 조기 반환되므로 추가 API 호출이 없어야 합니다.
    mock_sqs.get_current_price.assert_not_called()

async def test_generate_premium_watchlist_sorting_with_tie_score(mock_deps, tmp_path):
    """generate_premium_watchlist: 총점이 같을 경우 회전율(거래대금/시총)이 높은 순으로 정렬되는지 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)

    # 1. 전체 종목 리스트 Mock
    mapper.df = pd.DataFrame({
        "종목코드": ["A", "B"],
        "종목명": ["StockA", "StockB"],
        "시장구분": ["KOSPI", "KOSPI"]
    })

    # 2. 1차 필터 통과 가정 (get_current_stock_price)
    # 시가총액 조건 통과를 위해 적절한 값 반환
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"hts_avls": "5000", "stck_llam": "5000"})}
    )

    # 3. 2차 필터 (_analyze_candidate) Mock
    # 두 종목 모두 total_score는 50점으로 동일하게 설정
    # StockA: 시총 1000, 거래대금 100 -> 회전율 0.1
    # StockB: 시총 1000, 거래대금 500 -> 회전율 0.5 (더 높음) -> B가 먼저 와야 함
    item_a = OSBWatchlistItem(
        code="A", name="StockA", market="KOSPI",
        high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
        bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200,
        avg_trading_value_5d=100, 
        market_cap=1000,         
        total_score=50.0
    )
    item_b = OSBWatchlistItem(
        code="B", name="StockB", market="KOSPI",
        high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
        bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200,
        avg_trading_value_5d=500, 
        market_cap=1000,         
        total_score=50.0
    )

    async def mock_analyze(code, name, logger=None):
        if code == "A": return item_a
        if code == "B": return item_b
        return None

    # Redirect logs to tmp_path
    def mock_get_logger_side_effect(name, sub_dir=None):
        return get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    # 내부 메서드 모킹
    with patch.object(service, '_analyze_candidate', side_effect=mock_analyze), \
         patch.object(service, '_compute_rs_scores'), \
         patch.object(service, '_compute_profit_growth_scores', new_callable=AsyncMock), \
         patch.object(service, '_compute_total_scores'), \
         patch.object(service, '_save_premium_stocks') as mock_save, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect):
        
        await service.generate_premium_watchlist()
        
        # _save_premium_stocks(kospi, kosdaq) 호출 시 kospi 리스트의 순서 확인
        args, _ = mock_save.call_args
        kospi_list = args[0]
        
        assert len(kospi_list) == 2
        assert kospi_list[0].code == "B"  # 회전율 높은 B가 먼저
        assert kospi_list[1].code == "A"

async def test_load_premium_stocks_date_validation(mock_deps):
    """_load_premium_stocks: 날짜 유효성 검사 로직 검증 (경계값 테스트)."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 테스트용 파일 데이터 생성 함수
    def create_file_data(date_str):
        return json.dumps({
            "generated_date": date_str,
            "kospi": [],
            "kosdaq": []
        })

    # Case 1: 오늘 날짜 (유효)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 5, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240105"))):
        result = service._load_premium_stocks()
        assert isinstance(result, list)

    # Case 2: 2일 전 날짜 (유효 - 주말 포함 거래일 기준이므로 월요일에 금요일 파일도 유효)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 8, 10, 0, 0)  # 월요일
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240105"))):  # 금요일 (3일 전)
        result = service._load_premium_stocks()
        assert isinstance(result, list)

    # Case 3: 7일 전 날짜 (유효 - 경계값: 최장 연휴 커버)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 12, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240105"))):  # 7일 전
        result = service._load_premium_stocks()
        assert isinstance(result, list)

    # Case 4: 8일 전 날짜 (무효 - 7일 초과)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 13, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240105"))):  # 8일 전
        result = service._load_premium_stocks()
        assert result == []

    # Case 5: 연도가 바뀌는 경우 (12월 25일 생성 -> 1월 5일 로드: 11일 차이 -> 무효)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 5, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20231225"))):
        result = service._load_premium_stocks()
        assert result == []

async def test_analyze_candidate_rs_calculation(mock_deps):
    """_analyze_candidate: RS 값 계산 및 매핑 로직 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)

    # 1. 기본 Mock 설정 (필터 통과용)
    # 거래대금 조건(100억)을 만족하기 위해 volume을 충분히 크게 설정
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "3000"})}
    )
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90

    # 2. RS Mock 설정 (특정 수익률 반환)
    expected_rs = 15.5
    indicator.calc_rs_sync.return_value = expected_rs

    mapper.is_kosdaq.return_value = False

    # 실행
    item = await service._analyze_candidate("005930", "Samsung")

    # 검증
    assert item is not None
    assert item.rs_return_3m == expected_rs
    # indicator 서비스가 올바른 파라미터로 호출되었는지 확인
    indicator.calc_rs_sync.assert_called_once()
    call_args = indicator.calc_rs_sync.call_args
    assert call_args[0][0] == ohlcv # ohlcv_data
    assert call_args[1]['period_days'] == service._cfg.rs_period_days

async def test_analyze_candidate_rs_calculation_failure(mock_deps):
    """_analyze_candidate: RS 계산 실패 시 0.0 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)

    # 기본 Mock 설정 (필터 통과용)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "3000"})}
    )
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90

    # RS Mock 설정 (0.0 반환 - 데이터 부족 등)
    indicator.calc_rs_sync.return_value = 0.0

    mapper.is_kosdaq.return_value = False

    # 실행
    item = await service._analyze_candidate("005930", "Samsung")

    # 검증
    assert item is not None
    assert item.rs_return_3m == 0.0

def test_compute_rs_scores_logic(mock_deps):
    """_compute_rs_scores: 상위 퍼센타일에 점수 부여 로직 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 설정: 상위 20%에게 점수 부여
    service._cfg.rs_top_percentile = 20.0
    service._cfg.rs_score_points = 30.0

    # 10개의 아이템 생성 (RS 수익률 10~100)
    items = []
    for i in range(10):
        item = OSBWatchlistItem(
            code=f"{i}", name=f"Stock{i}", market="KOSPI",
            high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
            bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0,
            rs_return_3m=(i + 1) * 10.0  # 10.0, 20.0, ..., 100.0
        )
        items.append(item)
    
    # 실행
    service._compute_rs_scores(items)

    # 검증
    # 상위 20%는 10개 중 2개 (90.0, 100.0)
    # 정렬된 수익률: 10, 20, ..., 80, 90, 100
    # cutoff 인덱스 계산: len(10) * (1 - 0.2) = 8. index 8은 90.0
    # 따라서 90.0 이상인 항목만 점수를 받아야 함.
    
    for item in items:
        if item.rs_return_3m >= 90.0:
            assert item.rs_score == 30.0
        else:
            assert item.rs_score == 0.0

def test_compute_rs_scores_edge_cases(mock_deps):
    """_compute_rs_scores: 경계 조건(빈 리스트, 단일 아이템, 동점) 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Case 1: 빈 리스트
    items = []
    service._compute_rs_scores(items)
    assert len(items) == 0 # 에러 없이 통과

    # Case 2: 단일 아이템
    # 설정: 상위 10%에게 점수 부여
    service._cfg.rs_top_percentile = 10.0
    service._cfg.rs_score_points = 20.0
    
    item = OSBWatchlistItem(
        code="A", name="StockA", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0,
        rs_return_3m=50.0
    )
    items = [item]
    
    service._compute_rs_scores(items)
    
    # 로직상 1개 리스트에서는 항상 cutoff 조건을 만족하게 됨 (자기 자신이 cutoff)
    assert item.rs_score == 20.0

    # Case 3: 모든 RS가 동일한 경우
    items = [
        OSBWatchlistItem(code="A", name="A", market="KOSPI", high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0, bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0, rs_return_3m=10.0),
        OSBWatchlistItem(code="B", name="B", market="KOSPI", high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0, bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0, rs_return_3m=10.0),
    ]
    service._compute_rs_scores(items)
    
    # 모두 점수 획득해야 함
    for item in items:
        assert item.rs_score == 20.0

async def test_compute_profit_growth_scores_api_failure(mock_deps):
    """_compute_profit_growth_scores: API 호출 실패 시 점수 미부여 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 사용
    mock_scraper = MagicMock()
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)

    # 아이템 생성
    item = OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0
    )
    items = [item]
    
    # [수정] Scraper가 0.0을 반환하도록 설정 (실패/데이터 없음 간주)
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)

    # 실행
    await service._compute_profit_growth_scores(items)

    # 검증: 점수가 0이어야 함
    assert item.profit_growth_score == 0.0

async def test_compute_profit_growth_scores_exception(mock_deps):
    """_compute_profit_growth_scores: API 호출 중 예외 발생 시 점수 미부여 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 사용
    mock_scraper = MagicMock()
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)

    item = OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0
    )
    items = [item]
    
    # [수정] Scraper 예외 설정
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(side_effect=Exception("Scraping Error"))

    # 실행
    await service._compute_profit_growth_scores(items)

    # 검증: 점수가 0이어야 함 (예외가 발생해도 크래시되지 않고 0점 처리)
    assert item.profit_growth_score == 0.0

async def test_check_etf_ma_rising_logic(mock_deps):
    """_check_etf_ma_rising: ETF 이동평균 상승 여부 판단 로직 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Case 1: 데이터 부족
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[{"close": 100}] * 10)
    assert await service._check_etf_ma_rising("000000") is False
    
    # Case 2: 상승 추세 (MA가 3일 연속 상승)
    # period=20, days=3. 총 23일치 데이터 필요.
    # 간단히 close 가격이 계속 상승한다고 가정하면 MA도 상승함.
    data = [{"close": 100 + i} for i in range(30)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    assert await service._check_etf_ma_rising("000000") is True
    
    # Case 3: 하락 추세
    data = [{"close": 100 - i} for i in range(30)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    assert await service._check_etf_ma_rising("000000") is False

async def test_check_etf_ma_rising_exact_calculation(mock_deps):
    """_check_etf_ma_rising: MA 값의 연속 상승/하락을 정확히 계산하고 로그를 남기는지 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)

    # 설정: 5일 MA, 2일 연속 상승 필요 (총 3일간 MA값 비교)
    service._cfg.market_ma_period = 5
    service._cfg.market_ma_rising_days = 2

    # Case 1: 2일 연속 상승 (성공)
    logger.debug.reset_mock()
    # MA 값: (30, 40, 50) -> 상승
    closes_rising = [{"close": c} for c in [10, 20, 30, 40, 50, 60, 70]]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=closes_rising)
    assert await service._check_etf_ma_rising("RISING") is True

    # 로그 확인 (성공)
    log_call = logger.debug.call_args[0][0]
    assert log_call["event"] == "market_timing_check"
    assert log_call["is_rising"] is True
    assert "fail_detail" not in log_call

    # Case 2: 마지막 날 MA 하락 (실패)
    logger.debug.reset_mock()
    # MA 값: (30, 40, 36) -> 실패
    closes_falling = [{"close": c} for c in [10, 20, 30, 40, 50, 60, 0]]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=closes_falling)
    assert await service._check_etf_ma_rising("FALLING") is False

    # 로그 확인 (실패)
    log_call = logger.debug.call_args[0][0]
    assert log_call["event"] == "market_timing_check"
    assert log_call["is_rising"] is False
    assert "fail_detail" in log_call
    assert "MA decline: 40.00 -> 36.00" in log_call["fail_detail"]

    # Case 3: 중간에 MA 하락 (실패)
    logger.debug.reset_mock()
    # MA 값: (30, 28, 38) -> 실패
    closes_dip = [{"close": c} for c in [10, 20, 30, 40, 50, 0, 70]]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=closes_dip)
    assert await service._check_etf_ma_rising("DIP") is False

    # 로그 확인 (실패)
    log_call = logger.debug.call_args[0][0]
    assert log_call["event"] == "market_timing_check"
    assert log_call["is_rising"] is False
    assert "fail_detail" in log_call
    assert "MA decline: 30.00 -> 28.00" in log_call["fail_detail"]


async def test_build_daily_surge_pool_logic(mock_deps):
    """_build_daily_surge_pool: 실시간 랭킹 기반 Pool B 생성 및 필터링 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)

    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    # Mock API responses
    # 1. 거래대금 상위: A, B
    # 2. 상승률 상위: B, C
    # 3. 거래량 상위: Exception 발생 (네트워크 오류 등)
    sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"}]
    )
    sqs.get_top_rise_fall_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "B", "hts_kor_isnm": "StockB"}]
    )
    async def raise_network_error(*args, **kwargs):
        raise Exception("Network Error")
    sqs.get_top_volume_stocks.side_effect = raise_network_error
    
    # Pool A에 이미 "A"가 있다고 가정 -> "A"는 스킵되어야 함
    service._pool_a_items = {"A": MagicMock()}
    
    # _analyze_candidate Mock
    # B는 통과, C는 없음(위에서 C는 정의 안함, B만 정의함)
    # analyze는 B에 대해서만 호출될 것임 (A는 스킵)
    async def mock_analyze(code, name, logger=None):
        if code == "B":
            return OSBWatchlistItem(code="B", name="StockB", market="KOSPI",
                                    high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=1000,
                                    bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=100)
        return None
        
    with patch.object(service, '_analyze_candidate', side_effect=mock_analyze):
        pool_b = await service._build_daily_surge_pool()
        
        assert "A" not in pool_b # Pool A 중복 제외
        assert "B" in pool_b     # 분석 통과
        assert len(pool_b) == 1

def test_extract_op_profit_growth_logic(mock_deps):
    """_extract_op_profit_growth: 다양한 데이터 포맷 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Case 1: List of dicts
    data1 = [{"bsop_prti_icdc": "25.5"}]
    assert service._extract_op_profit_growth(data1) == 25.5
    
    # Case 2: Dict
    data2 = {"sale_totl_prfi_icdc": "10.0"}
    assert service._extract_op_profit_growth(data2) == 10.0
    
    # Case 3: 실제 API 응답 구조 (output 리스트 포함)
    data3 = {"rt_cd": "0", "msg1": "정상", "output": [{"stac_yymm": "202312", "bsop_prfi_inrt": "18.3"}]}
    assert service._extract_op_profit_growth(data3) == 18.3

    # Case 4: output에 bsop_prti_icdc 필드
    data4 = {"output": [{"bsop_prti_icdc": "30.0", "grs": "5.0"}]}
    assert service._extract_op_profit_growth(data4) == 30.0  # 우선순위 높은 키 사용

    # Case 5: Invalid data
    assert service._extract_op_profit_growth(None) == 0.0
    assert service._extract_op_profit_growth([]) == 0.0
    assert service._extract_op_profit_growth({}) == 0.0
    assert service._extract_op_profit_growth({"invalid_key": "100"}) == 0.0

async def test_save_load_premium_stocks_exceptions(mock_deps):
    """_save_premium_stocks, _load_premium_stocks: 예외 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Save Exception
    with patch("builtins.open", side_effect=IOError("Disk full")):
        service._save_premium_stocks([], [])
        logger.error.assert_called() # 에러 로그 호출 확인
        
    # Load Exception (Invalid JSON)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="{invalid_json")):
        items = service._load_premium_stocks()
        assert items == [] # 빈 리스트 반환

async def test_analyze_candidate_insufficient_data(mock_deps):
    """_analyze_candidate: 데이터 부족 시 None 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 데이터가 50개 미만
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[{"close": 100}] * 40)
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

async def test_update_market_timing_updates_cache(mock_deps):
    """_update_market_timing: KOSPI/KOSDAQ 각각에 대해 ETF MA 확인 후 캐시 업데이트 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # _check_etf_ma_rising 모킹
    with patch.object(service, '_check_etf_ma_rising', new_callable=AsyncMock) as mock_check:
        # KOSDAQ(True), KOSPI(False) 반환 설정
        mock_check.side_effect = [True, False]
        
        await service._update_market_timing()
        
        # 캐시 확인
        assert service._market_timing_cache["KOSDAQ"] is True
        assert service._market_timing_cache["KOSPI"] is False
        
        # 호출 확인
        expected_calls = [
            call(service._cfg.kosdaq_etf_code, logger=logger),
            call(service._cfg.kospi_etf_code, logger=logger)
        ]
        mock_check.assert_has_awaits(expected_calls, any_order=True)

async def test_analyze_candidate_trend_filter_fail(mock_deps):
    """_analyze_candidate: 정배열 조건(Close > MA20 > MA50) 불만족 시 탈락 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock
    # 역배열 데이터 생성 (가격이 계속 하락)
    # Close(100) < MA20(approx 110) < MA50(approx 125)
    ohlcv = [{"close": 200 - i, "high": 210 - i, "volume": 1000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

async def test_build_watchlist_merge_priority(mock_deps):
    """_build_watchlist: Pool A와 Pool B 병합 시 Pool A 우선순위 및 병합 로직 검증 (Line 114)."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Pool A 아이템 설정
    item_a_pool = OSBWatchlistItem(code="A", name="StockA_Pool", market="KOSPI",
                                   high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
                                   bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0)
    service._pool_a_items = {"A": item_a_pool}
    service._pool_a_loaded = True
    
    # Pool B 아이템 설정 (A가 중복됨)
    item_a_live = OSBWatchlistItem(code="A", name="StockA_Live", market="KOSPI",
                                   high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
                                   bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0)
    item_b_live = OSBWatchlistItem(code="B", name="StockB_Live", market="KOSPI",
                                   high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
                                   bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0)
    
    with patch.object(service, '_build_daily_surge_pool', new_callable=AsyncMock) as mock_build_b:
        mock_build_b.return_value = {"A": item_a_live, "B": item_b_live}
        
        await service._build_watchlist()
        
        # 검증
        watchlist = service._watchlist
        assert "A" in watchlist
        assert "B" in watchlist
        # A는 Pool A의 것이 유지되어야 함 (이름으로 확인)
        assert watchlist["A"].name == "StockA_Pool"
        assert watchlist["B"].name == "StockB_Live"

async def test_build_daily_surge_pool_skip_duplicates_and_dict_parsing(mock_deps):
    """_build_daily_surge_pool: API 응답이 dict일 때 파싱(Line 153) 및 중복 종목 스킵(Line 169) 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # Pool A에 이미 "A"가 존재한다고 설정
    service._pool_a_items = {"A": MagicMock()}
    
    # API 응답 Mock (dict 형태 데이터)
    # A: 중복 (스킵되어야 함)
    # B: 신규 (처리되어야 함)
    data = [
        {"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"},
        {"mksc_shrn_iscd": "B", "hts_kor_isnm": "StockB"}
    ]
    sqs.get_top_trading_value_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=data))
    sqs.get_top_rise_fall_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[]))
    sqs.get_top_volume_stocks = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[]))
    
    # _analyze_candidate Mock
    async def mock_analyze(code, name, logger=None):
        if code == "B":
            return OSBWatchlistItem(code="B", name="StockB", market="KOSPI",
                                    high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
                                    bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0)
        return None

    with patch.object(service, '_analyze_candidate', side_effect=mock_analyze) as mock_analyze_spy, \
         patch.object(service, '_compute_rs_scores'), \
         patch.object(service, '_compute_profit_growth_scores', new_callable=AsyncMock), \
         patch.object(service, '_compute_total_scores'):
        
        pool_b = await service._build_daily_surge_pool()
        
        # 검증
        # 1. A는 Pool A에 있으므로 스킵되어야 함 -> analyze 호출 안됨 (혹은 결과에 없음)
        # analyze 호출 인자 확인
        called_codes = [call_args[0][0] for call_args in mock_analyze_spy.call_args_list]
        assert "A" not in called_codes
        assert "B" in called_codes
        
        # 2. 결과 확인
        assert "A" not in pool_b
        assert "B" in pool_b

async def test_analyze_candidate_52w_high_filter_fail(mock_deps):
    """_analyze_candidate: 52주 고가 대비 너무 많이 하락한 경우 탈락 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (정배열 등 다른 조건은 만족시켜야 함)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # 2. 현재가 Mock
    # 현재가(prev_close) approx 1099.
    # 52주 고가 2000 -> (2000-1099)/2000 = 45% 하락 -> 탈락 (기본 설정 25% 가정)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "2000", "hts_avls": "3000"})}
    )
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

async def test_analyze_candidate_bb_squeeze_fail(mock_deps):
    """_analyze_candidate: 볼린저 밴드 스퀴즈 조건 불만족 시 탈락 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV & Price Mock (기본 통과 조건)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "3000"})}
    )
    
    # 2. BB Mock
    # 최근 폭이 최소 폭보다 훨씬 큼 (확장 국면): 처음 80개는 10, 마지막 10개는 50
    widths = [10.0] * 80 + [50.0] * 10
    indicator.calc_bb_widths_sync.return_value = widths

    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

async def test_is_market_timing_ok_caching(mock_deps):
    """is_market_timing_ok: 날짜 변경 시에만 업데이트 호출 및 캐싱 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    
    with patch.object(service, '_update_market_timing', new_callable=AsyncMock) as mock_update:
        # 1. 첫 호출 (캐시 없음)
        async def update_side_effect(*args, **kwargs):
            service._market_timing_cache["KOSPI"] = False
        mock_update.side_effect = update_side_effect
        
        result = await service.is_market_timing_ok("KOSPI")
        assert result is False
        mock_update.assert_awaited_once()
        assert service._market_timing_date == "20250101"
        
        # 2. 두 번째 호출 (같은 날짜 -> 캐시 사용)
        mock_update.reset_mock()
        result2 = await service.is_market_timing_ok("KOSPI")
        assert result2 is False
        mock_update.assert_not_called()
        
        # 3. 날짜 변경
        tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 10, 0, 0)
        result3 = await service.is_market_timing_ok("KOSPI")
        mock_update.assert_awaited_once()
        assert service._market_timing_date == "20250102"

def test_calc_turnover_ratio_zero_cap(mock_deps):
    """_calc_turnover_ratio: 시가총액 0일 때 0 반환 (ZeroDivisionError 방지)."""
    item = OSBWatchlistItem(
        code="A", name="A", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0,
        avg_trading_value_5d=100,
        market_cap=0 # Zero
    )
    assert OneilUniverseService._calc_turnover_ratio(item) == 0

async def test_analyze_candidate_insufficient_bb_data(mock_deps):
    """_analyze_candidate: BB 데이터가 부족할 때 None 반환."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    ohlcv = [{"close": 1000, "high": 1100, "volume": 100000000} for _ in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "1200", "hts_avls": "3000"})}
    )
    
    # BB Data Short (period=20이지만 10개만 반환)
    indicator.calc_bb_widths_sync.return_value = [20.0] * 10

    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

# ════════════════════════════════════════════════════════════════
# 추가된 테스트 케이스
# ════════════════════════════════════════════════════════════════

async def test_build_daily_surge_pool_partial_api_failure(mock_deps):
    """_build_daily_surge_pool: API 호출 중 일부가 실패해도 나머지는 처리되는지 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    # 3가지 랭킹 중 하나는 Exception, 하나는 실패 응답, 하나는 성공
    async def raise_network_error(*args, **kwargs):
        raise Exception("Network Error")
    sqs.get_top_trading_value_stocks.side_effect = raise_network_error
    sqs.get_top_rise_fall_stocks.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    sqs.get_top_volume_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"}]
    )
    
    # _analyze_candidate Mock (성공)
    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = OSBWatchlistItem(
            code="A", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=1000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=100
        )
        
        pool_b = await service._build_daily_surge_pool()
        
        assert "A" in pool_b
        assert len(pool_b) == 1

async def test_analyze_candidate_price_api_object_access(mock_deps):
    """_analyze_candidate: get_current_price 응답이 객체(속성 접근)일 때 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # OHLCV Mock
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # Price Mock (Object with attributes)
    class MockOutput:
        def __init__(self):
            self.stck_prpr = "1100"
            self.acml_vol = "1000000"
            self.pgtr_ntby_qty = "0"
            self.acml_tr_pbmn = "10000000000"
            self.stck_oprc = "1090"
            self.stck_lwpr = "1080"
            self.stck_prdy_clpr = "1090"
            self.w52_hgpr = "1200"
            self.hts_avls = "3000" # 3000억
            self.stck_llam = "0"

    mock_output = MockOutput()
    
    # resp.data가 dict이고 output이 object인 경우
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": mock_output}
    )
    
    # BB, RS Mock
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90
    indicator.calc_rs_sync.return_value = 10.0
    
    mapper.is_kosdaq.return_value = False

    item = await service._analyze_candidate("005930", "Samsung")
    assert item is not None
    assert item.market_cap == 300000000000

async def test_analyze_candidate_bb_data_integrity(mock_deps):
    """_analyze_candidate: BB 데이터에 None이 포함된 경우 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "3000"}}
    )
    
    # 1. 유효 데이터 부족 -> None 반환 (period=20이지만 5개만 반환)
    indicator.calc_bb_widths_sync.return_value = [20.0] * 5
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is None

    # 2. 유효 데이터 충분 -> 성공
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90
    indicator.calc_rs_sync.return_value = 10.0
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is not None

async def test_generate_premium_watchlist_fallback_market_cap(mock_deps, tmp_path):
    """generate_premium_watchlist: hts_avls(시가총액) 누락 시 stck_llam(상장주식수) 사용 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    mapper.df = pd.DataFrame({
        "종목코드": ["000001"], "종목명": ["StockA"], "시장구분": ["KOSPI"]
    })
    
    # hts_avls 없음, stck_llam 있음 (5000 -> 5000억으로 처리되는지 확인)
    async def mock_get_price(code, **kwargs):
        return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"hts_avls": "", "stck_llam": "5000"})})
    
    sqs.get_current_price.side_effect = mock_get_price
    
    # Redirect logs
    def mock_get_logger_side_effect(name, sub_dir=None):
        return get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect), \
         patch.object(service, '_save_premium_stocks'):
        
        mock_analyze.return_value = OSBWatchlistItem(
            code="000001", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=20000000000,
            market_cap=500000000000
        )
        
        result = await service.generate_premium_watchlist()
        
        # passed_first가 1이어야 함 (5000 * 1억 = 5000억 -> 범위 내)
        assert result['passed_first'] == 1

def test_should_refresh_watchlist_logic(mock_deps):
    """_should_refresh_watchlist: 설정된 시간에 따른 갱신 트리거 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    service._cfg.watchlist_refresh_minutes = [10, 30, 60]
    service._watchlist_refresh_done = set()
    
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    
    # 1. 5분 경과 -> False
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 5, 0)
    assert service._should_refresh_watchlist() is False
    
    # 2. 10분 경과 -> True
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 10, 0)
    assert service._should_refresh_watchlist() is True
    assert 10 in service._watchlist_refresh_done
    
    # 3. 10분 경과 (재호출) -> False (이미 수행됨)
    assert service._should_refresh_watchlist() is False
    
    # 4. 35분 경과 -> True (30분 트리거)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 35, 0)
    assert service._should_refresh_watchlist() is True
    assert 30 in service._watchlist_refresh_done

async def test_build_daily_surge_pool_analyze_exception(mock_deps):
    """_build_daily_surge_pool: _analyze_candidate 예외 발생 시 건너뛰기 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"}]
    )
    sqs.get_top_rise_fall_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    sqs.get_top_volume_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    
    with patch.object(service, '_analyze_candidate', side_effect=Exception("Analysis Error")):
        pool_b = await service._build_daily_surge_pool()
        assert len(pool_b) == 0

async def test_analyze_candidate_price_api_failure(mock_deps):
    """_analyze_candidate: 현재가 API 호출 실패 시 None 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    # OHLCV OK
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # Price API Fail
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    )
    
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is None

async def test_analyze_candidate_no_price_output(mock_deps):
    """_analyze_candidate: 현재가 API 성공이나 output 없음 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # Output None
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": None}
    )
    
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is None

async def test_analyze_candidate_w52_hgpr_zero(mock_deps):
    """_analyze_candidate: 52주 고가가 0일 때 (신규 상장 등) 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 100000000} for i in range(100)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # w52_hgpr = 0
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"w52_hgpr": "0", "hts_avls": "3000"})}
    )
    
    # BB, RS Mock (통과 조건)
    indicator.calc_bb_widths_sync.return_value = [20.0] * 90
    indicator.calc_rs_sync.return_value = 10.0
    
    item = await service._analyze_candidate("005930", "Samsung", logger=logger)
    assert item is not None
    # w52_hgpr이 0이어도 다른 조건 만족하면 통과 (52주 고가 근접 체크 스킵)

async def test_build_daily_surge_pool_response_items_as_objects(mock_deps):
    """_build_daily_surge_pool: API 응답 아이템이 객체(속성 접근)일 때 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    class MockItem:
        def __init__(self, code, name):
            self.mksc_shrn_iscd = code
            self.hts_kor_isnm = name
            
    # 객체 리스트 반환
    data = [MockItem("A", "StockA")]
    sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=data)
    sqs.get_top_rise_fall_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    sqs.get_top_volume_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    
    # Analyze Mock
    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = OSBWatchlistItem(
            code="A", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=1000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=100
        )
        
        pool_b = await service._build_daily_surge_pool()
        
        assert "A" in pool_b
        assert pool_b["A"].name == "StockA"

def test_extract_op_profit_growth_non_dict(mock_deps):
    """_extract_op_profit_growth: dict가 아닌 데이터 입력 시 0.0 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    class Obj:
        pass
    
    data = [Obj()]
    assert service._extract_op_profit_growth(data) == 0.0

async def test_generate_premium_watchlist_api_failure_in_loop(mock_deps, tmp_path):
    """generate_premium_watchlist: 1차 필터 루프 중 API 실패 시 건너뛰기 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    mapper.df = pd.DataFrame({
        "종목코드": ["A", "B"], "종목명": ["StockA", "StockB"], "시장구분": ["KOSPI", "KOSPI"]
    })
    
    # A: 성공, B: 실패
    async def mock_get_price(code, **kwargs):
        if code == "A":
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": create_mock_stock_info({"hts_avls": "5000"})})
        return ResCommonResponse(rt_cd="1", msg1="Fail")
    
    sqs.get_current_price.side_effect = mock_get_price
    
    # Redirect logs
    def mock_get_logger_side_effect(name, sub_dir=None):
        return get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect), \
         patch.object(service, '_save_premium_stocks'):
        
        mock_analyze.return_value = OSBWatchlistItem(
            code="A", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=1000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=100,
            market_cap=500000000000
        )
        
        result = await service.generate_premium_watchlist()
        
        # A만 통과
        assert result['passed_first'] == 1
        assert result['total_scanned'] == 2

async def test_analyze_candidate_ohlcv_none(mock_deps):
    """_analyze_candidate: OHLCV 데이터가 None일 때 None 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    sqs.get_recent_daily_ohlcv.return_value = None
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

async def test_generate_premium_watchlist_price_output_as_object(mock_deps, tmp_path):
    """generate_premium_watchlist: 현재가 API 응답이 객체일 때 처리 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    
    # [수정] Scraper Mock 추가
    mock_scraper = MagicMock()
    mock_scraper.fetch_yoy_profit_growth = AsyncMock(return_value=0.0)
    service = OneilUniverseService(sqs, indicator, mapper, tm, scraper_service=mock_scraper, logger=logger)
    
    mapper.df = pd.DataFrame({
        "종목코드": ["000001"], "종목명": ["StockA"], "시장구분": ["KOSPI"]
    })
    
    class MockOutput:
        def __init__(self):
            self.hts_avls = "5000" # 5000억
            self.stck_llam = "0"
    
    async def mock_get_price(code, **kwargs):
        return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": MockOutput()})
    
    sqs.get_current_price.side_effect = mock_get_price
    
    # Redirect logs
    def mock_get_logger_side_effect(name, sub_dir=None):
        return get_strategy_logger(name, log_dir=str(tmp_path), sub_dir=sub_dir)

    with patch.object(service, '_analyze_candidate', new_callable=AsyncMock) as mock_analyze, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect), \
         patch.object(service, '_save_premium_stocks'):
        
        mock_analyze.return_value = OSBWatchlistItem(
            code="000001", name="StockA", market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
            bb_width_min_20d=10, prev_bb_width=11, w52_hgpr=1200, avg_trading_value_5d=20000000000,
            market_cap=500000000000
        )
        
        result = await service.generate_premium_watchlist()
        
        assert result['passed_first'] == 1

def test_load_premium_stocks_file_not_found(mock_deps):
    """_load_premium_stocks: 파일이 없을 때 빈 리스트 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    with patch("os.path.exists", return_value=False):
        result = service._load_premium_stocks()
        assert result == []

def test_load_premium_stocks_malformed_date(mock_deps):
    """_load_premium_stocks: 날짜 형식이 잘못되었을 때 빈 리스트 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    file_data = json.dumps({
        "generated_date": "invalid-date",
        "kospi": [],
        "kosdaq": []
    })
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=file_data)):
        result = service._load_premium_stocks()
        assert result == []

async def test_check_etf_ma_rising_ohlcv_none(mock_deps):
    """_check_etf_ma_rising: OHLCV 데이터가 None일 때 False 반환 검증."""
    _, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(sqs, indicator, mapper, tm, logger=logger)
    
    sqs.get_recent_daily_ohlcv.return_value = None
    
    result = await service._check_etf_ma_rising("000000")
    assert result is False
