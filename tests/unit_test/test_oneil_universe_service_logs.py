import pytest
from unittest.mock import MagicMock, AsyncMock
from services.oneil_universe_service import OneilUniverseService
from strategies.oneil_common_types import OneilUniverseConfig, OSBWatchlistItem
from common.types import ResCommonResponse, ErrorCode

@pytest.fixture
def mock_components():
    ts = MagicMock()
    ts.get_recent_daily_ohlcv = AsyncMock()
    ts.get_financial_ratio = AsyncMock()
    ts.get_top_trading_value_stocks = AsyncMock()
    ts.get_top_rise_fall_stocks = AsyncMock()
    ts.get_top_volume_stocks = AsyncMock()
    
    sqs = MagicMock()
    indicator = MagicMock()
    mapper = MagicMock()
    tm = MagicMock()
    logger = MagicMock()
    
    return ts, sqs, indicator, mapper, tm, logger

@pytest.fixture
def service(mock_components):
    ts, sqs, indicator, mapper, tm, logger = mock_components
    config = OneilUniverseConfig()
    # 테스트 편의를 위해 설정값 일부 조정 가능
    return OneilUniverseService(ts, sqs, indicator, mapper, tm, config=config, logger=logger)

@pytest.mark.asyncio
async def test_check_etf_ma_rising_logs(service, mock_components):
    """_check_etf_ma_rising 메서드의 마켓 타이밍 체크 로그 검증"""
    ts, _, _, _, _, logger = mock_components
    
    # Mock OHLCV data: 가격이 상승하여 MA가 상승하는 시나리오
    # MA Period(20) + Rising Days(3) + Buffer
    closes = [100 + i for i in range(30)] 
    ohlcv = [{"close": c} for c in closes]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
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
    ts, _, _, _, _, logger = mock_components
    
    items = [
        OSBWatchlistItem(code="C1", name="N1", market="KOSPI", high_20d=100, ma_20d=90, ma_50d=80, avg_vol_20d=1000, bb_width_min_20d=1, prev_bb_width=1, w52_hgpr=120, avg_trading_value_5d=100)
    ]
    
    # Mock: 영업이익 증가율 30% (기준 25% 초과)
    mock_resp = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data=[{"op_profit_growth": "30.0"}])
    ts.get_financial_ratio.return_value = mock_resp
    
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