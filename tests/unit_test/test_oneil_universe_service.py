# tests/unit_test/test_oneil_universe_service.py
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, mock_open
from datetime import datetime
import pandas as pd
import json

from common.types import ResCommonResponse, ErrorCode
from common.types import ResStockFullInfoApiOutput, ResBollingerBand, ResRelativeStrength
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OSBWatchlistItem
from core.logger import get_strategy_logger

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
    mock_price_output = ResStockFullInfoApiOutput.from_dict({"w52_hgpr": "12000", "hts_avls": "1000"})
    mock_ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data={"output": mock_price_output}
    )
    mock_indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
        data=[ResBollingerBand(code="TEST", date="20231231", close=1, upper=1, lower=1, middle=1)] * 90
    )
    mock_indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
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
    # 거래대금 조건(100억)을 만족하기 위해 volume을 충분히 크게 설정 (1000원 * 15,000,000주 = 150억)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 15000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 2. 현재가 Mock (52주 고가 대비 20% 이내)
    # 현재가(prev_close)는 약 1099. 52주 고가 1200이면 통과.
    # 시가총액 1000억으로 가정 (hts_avls는 억 단위)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
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
async def test_generate_pool_a(mock_deps, tmp_path):
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
            return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"hts_avls": "5000", "acml_tr_pbmn": "20000000000"}})
        # 시가총액 10억, 거래대금 100원
        return ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"hts_avls": "10", "acml_tr_pbmn": "100"}})
    
    ts.get_current_stock_price.side_effect = mock_get_price
    
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

@pytest.mark.asyncio
async def test_generate_pool_a_sorting_with_tie_score(mock_deps, tmp_path):
    """generate_pool_a: 총점이 같을 경우 회전율(거래대금/시총)이 높은 순으로 정렬되는지 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)

    # 1. 전체 종목 리스트 Mock
    mapper.df = pd.DataFrame({
        "종목코드": ["A", "B"],
        "종목명": ["StockA", "StockB"],
        "시장구분": ["KOSPI", "KOSPI"]
    })

    # 2. 1차 필터 통과 가정 (get_current_stock_price)
    # 시가총액 조건 통과를 위해 적절한 값 반환
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"hts_avls": "5000", "stck_llam": "5000"}} 
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
         patch.object(service, '_save_pool_a') as mock_save, \
         patch("services.oneil_universe_service.get_strategy_logger", side_effect=mock_get_logger_side_effect):
        
        await service.generate_pool_a()
        
        # _save_pool_a(kospi, kosdaq) 호출 시 kospi 리스트의 순서 확인
        args, _ = mock_save.call_args
        kospi_list = args[0]
        
        assert len(kospi_list) == 2
        assert kospi_list[0].code == "B"  # 회전율 높은 B가 먼저
        assert kospi_list[1].code == "A"

@pytest.mark.asyncio
async def test_load_pool_a_date_validation(mock_deps):
    """_load_pool_a: 날짜 유효성 검사 로직 검증 (경계값 테스트)."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
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
        result = service._load_pool_a()
        assert isinstance(result, list)

    # Case 2: 어제 날짜 (유효 - 차이 1일)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 5, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240104"))):
        result = service._load_pool_a()
        assert isinstance(result, list)

    # Case 3: 2일 전 날짜 (무효)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 5, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20240103"))):
        result = service._load_pool_a()
        assert result == []

    # Case 4: 연도가 바뀌는 경우 (12월 31일 생성 -> 1월 2일 로드: 2일 차이 -> 무효)
    tm.get_current_kst_time.return_value = datetime(2024, 1, 2, 10, 0, 0)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=create_file_data("20231231"))):
        result = service._load_pool_a()
        assert result == []

@pytest.mark.asyncio
async def test_analyze_candidate_rs_calculation(mock_deps):
    """_analyze_candidate: RS 값 계산 및 매핑 로직 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)

    # 1. 기본 Mock 설정 (필터 통과용)
    # 거래대금 조건(100억)을 만족하기 위해 volume을 충분히 크게 설정
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 15000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
    )
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[MagicMock(upper=110, lower=90) for _ in range(30)]
    )

    # 2. RS Mock 설정 (특정 수익률 반환)
    expected_rs = 15.5
    indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=MagicMock(return_pct=expected_rs)
    )
    
    mapper.is_kosdaq.return_value = False

    # 실행
    item = await service._analyze_candidate("005930", "Samsung")

    # 검증
    assert item is not None
    assert item.rs_return_3m == expected_rs
    # indicator 서비스가 올바른 파라미터로 호출되었는지 확인
    indicator.get_relative_strength.assert_awaited_once()
    call_args = indicator.get_relative_strength.call_args
    assert call_args[0][0] == "005930" # code
    assert call_args[1]['period_days'] == service._cfg.rs_period_days
    assert call_args[1]['ohlcv_data'] == ohlcv

@pytest.mark.asyncio
async def test_analyze_candidate_rs_calculation_failure(mock_deps):
    """_analyze_candidate: RS 계산 실패 시 0.0 처리 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)

    # 기본 Mock 설정 (필터 통과용)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 15000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
    )
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[MagicMock(upper=110, lower=90) for _ in range(30)]
    )

    # RS Mock 설정 (실패 응답)
    indicator.get_relative_strength.return_value = ResCommonResponse(
        rt_cd="1", msg1="Fail", data=None
    )
    
    mapper.is_kosdaq.return_value = False

    # 실행
    item = await service._analyze_candidate("005930", "Samsung")

    # 검증
    assert item is not None
    assert item.rs_return_3m == 0.0

def test_compute_rs_scores_logic(mock_deps):
    """_compute_rs_scores: 상위 퍼센타일에 점수 부여 로직 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
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
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
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

@pytest.mark.asyncio
async def test_compute_profit_growth_scores_api_failure(mock_deps):
    """_compute_profit_growth_scores: API 호출 실패 시 점수 미부여 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)

    # 아이템 생성
    item = OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0
    )
    items = [item]

    # API 실패 응답 설정
    ts.get_financial_ratio.return_value = ResCommonResponse(
        rt_cd="1", msg1="API Error", data=None
    )

    # 실행
    await service._compute_profit_growth_scores(items)

    # 검증: 점수가 0이어야 함
    assert item.profit_growth_score == 0.0

@pytest.mark.asyncio
async def test_compute_profit_growth_scores_exception(mock_deps):
    """_compute_profit_growth_scores: API 호출 중 예외 발생 시 점수 미부여 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)

    item = OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=0, ma_20d=0, ma_50d=0, avg_vol_20d=0,
        bb_width_min_20d=0, prev_bb_width=0, w52_hgpr=0, avg_trading_value_5d=0
    )
    items = [item]

    # API 예외 설정
    ts.get_financial_ratio.side_effect = Exception("Network Error")

    # 실행
    await service._compute_profit_growth_scores(items)

    # 검증: 점수가 0이어야 함 (예외가 발생해도 크래시되지 않고 0점 처리)
    assert item.profit_growth_score == 0.0

@pytest.mark.asyncio
async def test_check_etf_ma_rising_logic(mock_deps):
    """_check_etf_ma_rising: ETF 이동평균 상승 여부 판단 로직 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # Case 1: 데이터 부족
    ts.get_recent_daily_ohlcv.return_value = [{"close": 100}] * 10 # period(20) + days(3) 보다 적음
    assert await service._check_etf_ma_rising("000000") is False
    
    # Case 2: 상승 추세 (MA가 3일 연속 상승)
    # period=20, days=3. 총 23일치 데이터 필요.
    # 간단히 close 가격이 계속 상승한다고 가정하면 MA도 상승함.
    data = [{"close": 100 + i} for i in range(30)]
    ts.get_recent_daily_ohlcv.return_value = data
    assert await service._check_etf_ma_rising("000000") is True
    
    # Case 3: 하락 추세
    data = [{"close": 100 - i} for i in range(30)]
    ts.get_recent_daily_ohlcv.return_value = data
    assert await service._check_etf_ma_rising("000000") is False

@pytest.mark.asyncio
async def test_build_pool_b_logic(mock_deps):
    """_build_pool_b: 실시간 랭킹 기반 Pool B 생성 및 필터링 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    
    # Ensure methods are AsyncMock
    ts.get_top_trading_value_stocks = AsyncMock()
    ts.get_top_rise_fall_stocks = AsyncMock()
    ts.get_top_volume_stocks = AsyncMock()
    
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # Mock API responses
    # 1. 거래대금 상위: A, B
    # 2. 상승률 상위: B, C
    # 3. 거래량 상위: Exception 발생 (네트워크 오류 등)
    ts.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "A", "hts_kor_isnm": "StockA"}]
    )
    ts.get_top_rise_fall_stocks.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"mksc_shrn_iscd": "B", "hts_kor_isnm": "StockB"}]
    )
    ts.get_top_volume_stocks.side_effect = Exception("Network Error")
    
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
        pool_b = await service._build_pool_b()
        
        assert "A" not in pool_b # Pool A 중복 제외
        assert "B" in pool_b     # 분석 통과
        assert len(pool_b) == 1

def test_extract_op_profit_growth_logic(mock_deps):
    """_extract_op_profit_growth: 다양한 데이터 포맷 처리 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # Case 1: List of dicts
    data1 = [{"bsop_prti_icdc": "25.5"}]
    assert service._extract_op_profit_growth(data1) == 25.5
    
    # Case 2: Dict
    data2 = {"sale_totl_prfi_icdc": "10.0"}
    assert service._extract_op_profit_growth(data2) == 10.0
    
    # Case 3: Invalid data
    assert service._extract_op_profit_growth(None) == 0.0
    assert service._extract_op_profit_growth([]) == 0.0
    assert service._extract_op_profit_growth({}) == 0.0
    assert service._extract_op_profit_growth({"invalid_key": "100"}) == 0.0

@pytest.mark.asyncio
async def test_save_load_pool_a_exceptions(mock_deps):
    """_save_pool_a, _load_pool_a: 예외 처리 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # Save Exception
    with patch("builtins.open", side_effect=IOError("Disk full")):
        service._save_pool_a([], [])
        logger.error.assert_called() # 에러 로그 호출 확인
        
    # Load Exception (Invalid JSON)
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data="{invalid_json")):
        items = service._load_pool_a()
        assert items == [] # 빈 리스트 반환

@pytest.mark.asyncio
async def test_analyze_candidate_insufficient_data(mock_deps):
    """_analyze_candidate: 데이터 부족 시 None 반환 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 데이터가 50개 미만
    ts.get_recent_daily_ohlcv.return_value = [{"close": 100}] * 40
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None
    # 로그가 호출되었는지 확인 (debug 레벨)
    logger.debug.assert_called()

@pytest.mark.asyncio
async def test_analyze_candidate_trend_filter_fail(mock_deps):
    """_analyze_candidate: 정배열 조건(Close > MA20 > MA50) 불만족 시 탈락 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock
    # 역배열 데이터 생성 (가격이 계속 하락)
    # Close(100) < MA20(approx 110) < MA50(approx 125)
    ohlcv = [{"close": 200 - i, "high": 210 - i, "volume": 1000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

@pytest.mark.asyncio
async def test_analyze_candidate_52w_high_filter_fail(mock_deps):
    """_analyze_candidate: 52주 고가 대비 너무 많이 하락한 경우 탈락 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV Mock (정배열 등 다른 조건은 만족시켜야 함)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 1000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 2. 현재가 Mock
    # 현재가(prev_close) approx 1099.
    # 52주 고가 2000 -> (2000-1099)/2000 = 45% 하락 -> 탈락 (기본 설정 25% 가정)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "2000", "hts_avls": "1000"}}
    )
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

@pytest.mark.asyncio
async def test_analyze_candidate_bb_squeeze_fail(mock_deps):
    """_analyze_candidate: 볼린저 밴드 스퀴즈 조건 불만족 시 탈락 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    # 1. OHLCV & Price Mock (기본 통과 조건)
    ohlcv = [{"close": 1000 + i, "high": 1100 + i, "volume": 1000000} for i in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
    )
    
    # 2. BB Mock
    # 최근 폭이 최소 폭보다 훨씬 큼 (확장 국면)
    bands = []
    for i in range(50):
        width = 10 if i < 40 else 50 # 최근에 확 벌어짐
        bands.append(MagicMock(upper=100+width, lower=100))
    
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=bands
    )
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None

@pytest.mark.asyncio
async def test_is_market_timing_ok_caching(mock_deps):
    """is_market_timing_ok: 날짜 변경 시에만 업데이트 호출 및 캐싱 검증."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 10, 0, 0)
    
    with patch.object(service, '_update_market_timing', new_callable=AsyncMock) as mock_update:
        # 1. 첫 호출 (캐시 없음)
        async def update_side_effect():
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

@pytest.mark.asyncio
async def test_analyze_candidate_insufficient_bb_data(mock_deps):
    """_analyze_candidate: BB 데이터가 부족할 때 None 반환."""
    ts, sqs, indicator, mapper, tm, logger = mock_deps
    service = OneilUniverseService(ts, sqs, indicator, mapper, tm, logger=logger)
    
    ohlcv = [{"close": 1000, "high": 1100, "volume": 1000000} for _ in range(100)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"w52_hgpr": "1200", "hts_avls": "1000"}}
    )
    
    # BB Data Short
    bands = [MagicMock(upper=110, lower=90) for _ in range(10)]
    indicator.get_bollinger_bands.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=bands
    )
    
    item = await service._analyze_candidate("CODE", "Name", logger=logger)
    assert item is None
