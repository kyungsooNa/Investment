import pytest
from unittest.mock import MagicMock, AsyncMock
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OneilUniverseConfig, OSBWatchlistItem
from common.types import ResCommonResponse, ErrorCode

@pytest.fixture
def mock_components():
    # ts = MagicMock() # Removed
    
    sqs = MagicMock()
    sqs.get_recent_daily_ohlcv = AsyncMock()
    sqs.get_financial_ratio = AsyncMock()
    sqs.get_top_trading_value_stocks = AsyncMock()
    sqs.get_top_rise_fall_stocks = AsyncMock()
    sqs.get_top_volume_stocks = AsyncMock()
    
    indicator = MagicMock()
    mapper = MagicMock()
    tm = MagicMock()
    logger = MagicMock()
    
    return None, sqs, indicator, mapper, tm, logger

@pytest.fixture
def service(mock_components):
    _, sqs, indicator, mapper, tm, logger = mock_components
    config = OneilUniverseConfig()
    # 테스트 편의를 위해 설정값 일부 조정 가능
    return OneilUniverseService(sqs, indicator, mapper, tm, config=config, logger=logger)

@pytest.mark.asyncio
async def test_check_etf_ma_rising_logs(service, mock_components):
    """_check_etf_ma_rising 메서드의 마켓 타이밍 체크 로그 검증"""
    _, sqs, _, _, _, logger = mock_components
    
    # Mock OHLCV data: 가격이 상승하여 MA가 상승하는 시나리오
    # MA Period(20) + Rising Days(3) + Buffer
    closes = [100 + i for i in range(30)] 
    ohlcv = [{"close": c} for c in closes]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    result = await service._check_etf_ma_rising("TEST_ETF")
    
    assert result is True
    
    # 로그 확인
    found_log = False
    for call_args in logger.debug.call_args_list:
        arg = call_args[0][0]
        if isinstance(arg, dict) and arg.get("event") == "market_timing_check":
            assert arg["etf_code"] == "TEST_ETF"
            assert arg["is_rising"] is True
            assert "ma_values" in arg
            found_log = True
            break
    assert found_log, "market_timing_check 이벤트 로그가 기록되지 않았습니다."

def test_compute_rs_scores_logs(service, mock_components):
    """_compute_rs_scores 메서드의 스코어링 로그 검증"""
    _, _, _, _, _, logger = mock_components
    
    # 10개의 아이템 생성 (RS 수익률 0~9)
    # 상위 10% 설정 시, 수익률 9인 아이템만 점수를 받아야 함
    items = [
        OSBWatchlistItem(
            code=f"C{i}", name=f"N{i}", market="KOSPI", high_20d=100, ma_20d=90, ma_50d=80, 
            avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, 
            avg_trading_value_5d=100, rs_return_3m=float(i)
        )
        for i in range(10)
    ]
    
    service._compute_rs_scores(items)
    
    # 시작 로그 확인
    start_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "compute_rs_scores_started"), None)
    assert start_log is not None
    assert start_log["item_count"] == 10
    
    # 상세 계산 로그 확인
    details_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "rs_score_calculation_details"), None)
    assert details_log is not None
    
    # 점수 부여 로그 확인 (상위 10%인 C9 종목)
    assigned_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "rs_score_assigned" and c[0][0].get("code") == "C9"), None)
    assert assigned_log is not None
    assert assigned_log["score"] == service._cfg.rs_score_points

@pytest.mark.asyncio
async def test_compute_profit_growth_scores_logs(service, mock_components):
    """_compute_profit_growth_scores 메서드의 로그 검증"""
    _, sqs, _, _, _, logger = mock_components
    
    items = [
        OSBWatchlistItem(code="C1", name="N1", market="KOSPI", high_20d=100, ma_20d=90, ma_50d=80, avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, avg_trading_value_5d=100)
    ]
    
    # Mock: 영업이익 증가율 30% (기준 25% 초과)
    mock_resp = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data=[{"op_profit_growth": "30.0"}])
    sqs.get_financial_ratio.return_value = mock_resp
    
    await service._compute_profit_growth_scores(items)
    
    # 시작 로그
    start_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "compute_profit_growth_scores_started"), None)
    assert start_log is not None
    
    # 점수 부여 로그
    assigned_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "profit_growth_score_assigned"), None)
    assert assigned_log is not None
    assert assigned_log["code"] == "C1"
    assert assigned_log["growth_pct"] == 30.0

def test_compute_total_scores_logs(service, mock_components):
    """_compute_total_scores 메서드의 로그 검증"""
    _, _, _, _, _, logger = mock_components
    
    item = OSBWatchlistItem(code="C1", name="N1", market="KOSPI", high_20d=100, ma_20d=90, ma_50d=80, avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, avg_trading_value_5d=100)
    item.rs_score = 30.0
    item.profit_growth_score = 20.0
    
    service._compute_total_scores([item])
    
    # 총점 계산 로그
    calc_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "total_score_calculated"), None)
    assert calc_log is not None
    assert calc_log["code"] == "C1"
    assert calc_log["total_score"] == 50.0

@pytest.mark.asyncio
async def test_build_watchlist_sorted_logs(service, mock_components):
    """_build_watchlist 실행 시 정렬된 결과 로그 검증"""
    _, _, _, _, _, logger = mock_components
    
    # 내부 메서드 Mocking을 통해 복잡한 로직 우회
    # Pool A: 100점
    item_a = OSBWatchlistItem(code="A1", name="NA1", market="KOSPI", high_20d=100, ma_20d=90, ma_50d=80, avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, avg_trading_value_5d=100)
    item_a.total_score = 100
    service._load_pool_a = MagicMock(return_value=[item_a])
    
    # Pool B: 50점
    item_b = OSBWatchlistItem(code="B1", name="NB1", market="KOSDAQ", high_20d=100, ma_20d=90, ma_50d=80, avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, avg_trading_value_5d=100)
    item_b.total_score = 50
    service._build_pool_b = AsyncMock(return_value={"B1": item_b})
    
    await service._build_watchlist()
    
    # watchlist_sorted 로그 확인
    sorted_log = next((c[0][0] for c in logger.debug.call_args_list if isinstance(c[0][0], dict) and c[0][0].get("event") == "watchlist_sorted"), None)
    assert sorted_log is not None
    assert len(sorted_log["items"]) >= 2
    
    # 정렬 순서 확인 (점수 높은 A1이 먼저)
    assert sorted_log["items"][0]["code"] == "A1"
    assert sorted_log["items"][1]["code"] == "B1"

@pytest.mark.asyncio
async def test_build_pool_b_parallel_execution(mock_components):
    """_build_pool_b 메서드가 후보 종목들을 병렬로 처리하여 수집하는지 검증"""
    _, sqs, indicator, mapper, tm, logger = mock_components
    
    # 설정: 청크 사이즈를 2로 설정하여 5개 후보가 3번의 청크(2, 2, 1)로 나뉘어 처리되는지 간접 확인
    config = OneilUniverseConfig(api_chunk_size=2, pool_b_size=10)
    
    # PerformanceManager Mock
    pm = MagicMock()

    service = OneilUniverseService(
        stock_query_service=sqs,
        indicator_service=indicator,
        stock_code_mapper=mapper,
        time_manager=tm,
        config=config,
        logger=logger,
        performance_manager=pm
    )
    
    # Mock 데이터 설정
    # 랭킹 API 응답 Mocking (총 5개 후보 종목 생성)
    # 중복 제거 로직 테스트를 위해 일부러 겹치는 종목 없이 설정
    sqs.get_top_trading_value_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, 
        msg1="OK",
        data=[{"mksc_shrn_iscd": "000010", "hts_kor_isnm": "Stock1"}, 
              {"mksc_shrn_iscd": "000020", "hts_kor_isnm": "Stock2"}]
    )
    sqs.get_top_rise_fall_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, 
        msg1="OK",
        data=[{"mksc_shrn_iscd": "000030", "hts_kor_isnm": "Stock3"},
              {"mksc_shrn_iscd": "000040", "hts_kor_isnm": "Stock4"}]
    )
    sqs.get_top_volume_stocks.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, 
        msg1="OK",
        data=[{"mksc_shrn_iscd": "000050", "hts_kor_isnm": "Stock5"}]
    )
    
    # _analyze_candidate 메서드를 Mocking하여 실제 복잡한 분석 로직은 건너뛰고 성공 결과 반환
    async def mock_analyze(code, name, logger=None):
        return OSBWatchlistItem(
            code=code, name=name, market="KOSPI",
            high_20d=1000, ma_20d=900, ma_50d=800, avg_vol_20d=10000,
            bb_width_min_20d=10, prev_bb_width=12, w52_hgpr=1200,
            avg_trading_value_5d=10000000000, market_cap=500000000000
        )
    
    service._analyze_candidate = AsyncMock(side_effect=mock_analyze)
    
    # 실행
    pool_b = await service._build_pool_b()
    
    # 검증
    # 총 5개 후보에 대해 analyze가 호출되었는지 확인
    assert service._analyze_candidate.call_count == 5
    
    # 결과 딕셔너리에 5개가 모두 포함되었는지 확인
    assert len(pool_b) == 5
    assert "000010" in pool_b
    assert "000050" in pool_b