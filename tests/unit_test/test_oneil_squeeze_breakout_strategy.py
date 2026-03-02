# tests/unit_test/test_oneil_squeeze_breakout_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from common.types import ResCommonResponse, TradeSignal
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_common_types import OSBWatchlistItem, OSBPositionState

@pytest.fixture
def mock_strategy_deps():
    ts = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    logger = MagicMock()
    
    ts.get_current_stock_price = AsyncMock()
    ts.get_recent_daily_ohlcv = AsyncMock()
    universe.get_watchlist = AsyncMock()
    universe.is_market_timing_ok = AsyncMock()
    
    return ts, universe, tm, logger

@pytest.fixture
def breakout_candidate_item():
    """테스트용 돌파 후보 종목 아이템을 반환합니다."""
    return OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=70000, ma_20d=68000, ma_50d=65000,
        avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
        w52_hgpr=80000, avg_trading_value_5d=50000000000,
        market_cap=100_000_000_000 # 1000억 (테스트용)
    )

@pytest.fixture
def scan_setup(mock_strategy_deps, breakout_candidate_item):
    """scan 메서드 테스트를 위한 공통 설정 픽스처."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    
    # 테스트 격리를 위해 상태 초기화 및 파일 저장 방지
    strategy._position_state = {}
    strategy._save_state = MagicMock()
    
    # 1. 워치리스트 Mock (감시 대상 종목)
    universe.get_watchlist.return_value = {"005930": breakout_candidate_item}
    
    # 2. 마켓 타이밍 OK
    universe.is_market_timing_ok.return_value = True
    
    # 3. 장중 경과율 (50% 진행 가정)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    
    return strategy, ts, universe, tm, logger

@pytest.mark.asyncio
async def test_scan_buy_signal(scan_setup):
    """scan: 돌파 조건 충족 시 매수 시그널 생성 검증."""
    strategy, ts, _, _, _ = scan_setup
    
    # 4. 현재가 Mock (돌파 성공 케이스)
    # 가격: 71000 (> 20일고가 70000)
    # 거래량: 200000 (환산 400000 > 평균 100000 * 1.5)
    # 프로그램 수급: 30000 (> 0)
    # 프로그램 금액: 30000 * 71000 = 21.3억
    # 거래대금: 142억 -> 21.3/142 = 15% (> 10%)
    # 시총: 1000억 -> 21.3/1000 = 2.13% (> 0.5%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "30000", 
            "acml_tr_pbmn": "14200000000", "ccld_strg_val": "150.0"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 1
    assert signals[0].code == "005930"
    assert signals[0].action == "BUY"
    assert "체결강도 150.0%" in signals[0].reason
    assert signals[0].price == 71000
    # 내부 상태에 포지션 기록되었는지 확인
    assert "005930" in strategy._position_state

@pytest.mark.asyncio
async def test_scan_no_signal_if_price_not_breakout(scan_setup):
    """scan: 가격이 20일 고가를 돌파하지 못하면 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 가격 돌파 실패: 현재가 70000 <= 20일 고가 70000
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "70000", "acml_vol": "200000", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_buy_ratio_low(scan_setup):
    """scan: 프로그램 순매수 비중(거래대금 대비)이 낮으면 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 가격/거래량은 돌파, 순매수 수량도 양수지만 비중이 낮음
    # 순매수 1000주 * 71000 = 7100만원
    # 거래대금 142억 -> 0.5% (기준 10% 미달)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "1000", "acml_tr_pbmn": "14200000000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_market_cap_ratio_low(scan_setup):
    """scan: 프로그램 순매수 비중이 거래대금 조건은 만족하나 시가총액 조건 미달 시 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 시가총액: 1000억 (fixture 설정)
    # 프로그램 순매수: 6000주 * 71000원 = 4.26억
    #   -> 시총 대비: 4.26억 / 1000억 = 0.426% (< 0.5% 기준 미달)
    # 거래대금: 40억
    #   -> 거래대금 대비: 4.26억 / 40억 = 10.65% (> 10% 기준 만족)
    
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "6000", "acml_tr_pbmn": "4000000000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_execution_strength_low(scan_setup):
    """scan: 체결강도가 120 미만이면 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 모든 조건 통과, 하지만 체결강도 110.0 (< 120.0)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "30000", 
            "acml_tr_pbmn": "14200000000", "ccld_strg_val": "110.0"
        }}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_early_market_volume_defense(scan_setup):
    """scan: 장 초반(09:00~09:20) 거래량 뻥튀기 방어 및 최소 거래량 조건 테스트."""
    strategy, ts, _, tm, _ = scan_setup
    
    # 1. 장 시작 3분 후 (09:03) -> progress approx 3/390 = 0.0077
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 3, 0)
    
    # 설정: 평균 거래량 100,000
    # Defense 2 기준: 30,000 (30%)
    
    # Case A: 거래량 부족 (20,000 < 30,000)
    # 프로그램 수급은 충분하게 설정 (10000주 = 7.1억)
    # 거래대금 14.2억 -> 50% (>10%)
    # 시총 1000억 -> 0.71% (>0.5%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "20000", "pgtr_ntby_qty": "10000", "acml_tr_pbmn": "1420000000"
        }}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 0 # Defense 2에 의해 탈락

    # Case B: 거래량 충분 (40,000 > 30,000)
    # Defense 1 적용: 40,000 / 0.05 = 800,000 (> 150,000). 통과.
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "40000", "pgtr_ntby_qty": "10000", "acml_tr_pbmn": "2840000000"
        }}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 1 # 통과

@pytest.mark.asyncio
async def test_scan_no_signal_if_volume_not_breakout(scan_setup):
    """scan: 예상 거래량이 기준 미달이면 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 거래량 돌파 실패: 환산 거래량 100000 < 100000 * 1.5
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "50000", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_buy_low(scan_setup):
    """scan: 프로그램 순매수가 기준 미달이면 매수 시그널 없음."""
    strategy, ts, _, _, _ = scan_setup
    
    # 프로그램 수급 실패: 순매수 0 <= 기준 0
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "0"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_check_exits_stop_loss(mock_strategy_deps):
    """check_exits: 손절 조건(-5%) 도달 시 매도 시그널 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (손절가 도달: 9400원, -6%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9400"}}
    )
    
    # 3. 보유 종목 리스트 전달
    holdings = [{"code": "005930", "buy_price": 10000}]
    
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "손절" in signals[0].reason
    # 내부 상태에서 제거되었는지 확인
    assert "005930" not in strategy._position_state

@pytest.mark.asyncio
async def test_check_exits_trailing_stop(mock_strategy_deps):
    """check_exits: 트레일링 스탑(-8%) 조건 도달 시 매도 시그널 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정 (매수가 10000, 최고가 12000)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=12000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (트레일링 스탑 발동: 11000원, 최고가 대비 -8.3%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11000"}}
    )
    
    # 3. 보유 종목 리스트 전달
    holdings = [{"code": "005930", "buy_price": 10000}]
    
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "트레일링스탑" in signals[0].reason
    assert "005930" not in strategy._position_state

@pytest.mark.asyncio
async def test_check_exits_peak_price_update(mock_strategy_deps):
    """check_exits: 최고가 갱신 로직 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정 (최고가 11000)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=11000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (신고가 12000)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12000"}}
    )
    
    # 3. 보유 종목 리스트 전달
    holdings = [{"code": "005930", "buy_price": 10000}]
    
    signals = await strategy.check_exits(holdings)
    
    # 매도 시그널은 없어야 함
    assert len(signals) == 0
    # 최고가가 12000으로 갱신되었는지 확인
    assert strategy._position_state["005930"].peak_price == 12000

@pytest.mark.asyncio
async def test_scan_empty_watchlist(scan_setup):
    """scan: 워치리스트가 비어있을 때 빈 리스트 반환."""
    strategy, _, universe, _, _ = scan_setup
    universe.get_watchlist.return_value = {}
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_market_not_ready(scan_setup):
    """scan: 장 시작 전(경과율 <= 0)이면 빈 리스트 반환."""
    strategy, _, _, tm, _ = scan_setup
    # Open time same as current time -> elapsed 0
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_skip_existing_position(scan_setup):
    """scan: 이미 보유 중인 종목은 스캔 제외."""
    strategy, ts, _, _, _ = scan_setup
    # 005930을 보유 상태로 설정
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9000)
    
    # API 호출이 발생하지 않아야 함 (mock reset)
    ts.get_current_stock_price.reset_mock()
    
    signals = await strategy.scan()
    assert len(signals) == 0
    ts.get_current_stock_price.assert_not_called()

@pytest.mark.asyncio
async def test_scan_bad_market_timing(scan_setup):
    """scan: 마켓 타이밍이 좋지 않으면 스캔 제외."""
    strategy, ts, universe, _, _ = scan_setup
    universe.is_market_timing_ok.return_value = False
    
    signals = await strategy.scan()
    assert len(signals) == 0
    # 마켓 타이밍 체크 후 탈락하므로 가격 조회 API 호출 안함
    ts.get_current_stock_price.assert_not_called()

@pytest.mark.asyncio
async def test_scan_exception_handling(scan_setup):
    """scan: 개별 종목 처리 중 예외 발생 시 로그 남기고 계속 진행."""
    strategy, ts, _, _, logger = scan_setup
    # check_breakout 내부에서 호출되는 API가 예외 발생
    ts.get_current_stock_price.side_effect = Exception("API Error")
    
    signals = await strategy.scan()
    assert len(signals) == 0
    logger.error.assert_called()

@pytest.mark.asyncio
async def test_check_breakout_api_failure(scan_setup):
    """_check_breakout: 현재가 조회 실패 시 None 반환."""
    strategy, ts, _, _, _ = scan_setup
    # API 실패 응답
    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_check_exits_api_failure(mock_strategy_deps):
    """check_exits: 현재가 조회 실패 시 시그널 생성 안함."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9000)
    
    # API 실패
    ts.get_current_stock_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    
    holdings = [{"code": "005930", "buy_price": 10000}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 0

def test_calculate_qty_zero_price(mock_strategy_deps):
    """_calculate_qty: 가격이 0 이하일 때 1 반환."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    assert strategy._calculate_qty(0) == 1
    assert strategy._calculate_qty(-100) == 1

def test_load_save_state(mock_strategy_deps, tmp_path):
    """_load_state, _save_state: 파일 입출력 동작 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    
    # 임시 파일 경로 설정
    test_file = tmp_path / "test_state.json"
    strategy.STATE_FILE = str(test_file)
    
    # 상태 설정 및 저장
    strategy._position_state = {
        "005930": OSBPositionState(10000, "20250101", 11000, 9500)
    }
    strategy._save_state()
    
    assert test_file.exists()
    
    # 새로운 인스턴스에서 로드
    strategy2 = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy2.STATE_FILE = str(test_file)
    strategy2._position_state = {} # 초기화
    strategy2._load_state()
    
    assert "005930" in strategy2._position_state
    assert strategy2._position_state["005930"].peak_price == 11000

@pytest.mark.asyncio
async def test_check_time_stop_logic(mock_strategy_deps):
    """_check_time_stop: 시간 손절 로직 상세 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    
    # 설정: 5일 경과, 박스권 2%
    strategy._cfg.time_stop_days = 5
    strategy._cfg.time_stop_box_range_pct = 2.0
    
    state = OSBPositionState(entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=10000)
    
    # OHLCV Mock: 진입일(0101) 이후 5일치 데이터 생성 (0102~0106)
    ohlcv = [
        {"date": "20250101"}, # 진입일
        {"date": "20250102"}, {"date": "20250103"}, {"date": "20250104"}, {"date": "20250105"}, {"date": "20250106"} # 5일 경과
    ]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # Case 1: 5일 경과 & 횡보(10100원, +1%) & 급등이력 없음 -> True (손절)
    assert await strategy._check_time_stop("005930", state, 10100) is True
    
    # Case 2: 5일 경과 & 상승 이탈(10300원, +3%) -> False
    assert await strategy._check_time_stop("005930", state, 10300) is False
    
    # Case 3: 5일 경과 & 하락 이탈(9700원, -3%) -> True (상승 실패)
    # 전략 로직 변경(abs 제거)으로 인해 하락 상태여도 시간 손절 조건(상승 미달)에 해당함
    assert await strategy._check_time_stop("005930", state, 9700) is True
    
    # Case 4: 5일 경과 & 횡보(10100원) & 급등이력 있음(peak 10600원, +6%) -> False
    state_peak = OSBPositionState(entry_price=10000, entry_date="20250101", peak_price=10600, breakout_level=10000)
    assert await strategy._check_time_stop("005930", state_peak, 10100) is False
    
    # Case 5: 4일 경과 (데이터 부족) -> False
    ohlcv_short = ohlcv[:-1] # 4일치
    ts.get_recent_daily_ohlcv.return_value = ohlcv_short
    assert await strategy._check_time_stop("005930", state, 10100) is False

@pytest.mark.asyncio
async def test_check_exits_trend_break(mock_strategy_deps):
    """check_exits: 추세 이탈(10MA 하향 + 대량 거래량) 시 매도 시그널 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10500, breakout_level=10000
    )
    
    # 2. OHLCV Mock (10MA, 20AvgVol 계산용)
    # 20일치 데이터. 종가 11000, 거래량 100000으로 일정하다고 가정.
    # 10MA = 11000, 20AvgVol = 100000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(20)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 3. 현재가 Mock (추세 이탈 조건)
    # 가격: 10800 (< 10MA 11000) -> 가격 이탈
    # 거래량: 60000. 장 진행률 0.5 가정 -> 예상 120000 (> 100000) -> 거래량 이탈
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10800", "acml_vol": "60000"}}
    )
    
    # 장 진행률 50% 설정을 위해 시간 Mock
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0) # 12:00
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    
    # 4. 실행
    holdings = [{"code": "005930", "buy_price": 10000}]
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "추세이탈" in signals[0].reason
    assert "10MA" in signals[0].reason

@pytest.mark.asyncio
async def test_check_exits_trend_break_no_volume(mock_strategy_deps):
    """check_exits: 가격은 이탈했으나 거래량이 부족하면 매도 안 함."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10500, breakout_level=10000
    )
    
    # 10MA = 11000, 20AvgVol = 100000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(20)]
    ts.get_recent_daily_ohlcv.return_value = ohlcv
    
    # 가격: 10800 (< 11000) -> 가격 이탈
    # 거래량: 40000. 장 진행률 0.5 -> 예상 80000 (< 100000) -> 거래량 부족
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10800", "acml_vol": "40000"}}
    )
    
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    
    holdings = [{"code": "005930", "buy_price": 10000}]
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_mixed_market_timing(scan_setup):
    """scan: 시장별 마켓 타이밍이 다를 때(KOSPI X, KOSDAQ O) 동작 검증."""
    strategy, ts, universe, _, _ = scan_setup
    
    # 워치리스트: KOSPI 종목 1개, KOSDAQ 종목 1개
    item_kospi = OSBWatchlistItem(code="005930", name="Samsung", market="KOSPI",
                                  high_20d=70000, ma_20d=68000, ma_50d=65000,
                                  avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
                                  w52_hgpr=80000, avg_trading_value_5d=50000000000)
    item_kosdaq = OSBWatchlistItem(code="123456", name="KOSDAQ_Stock", market="KOSDAQ",
                                   high_20d=10000, ma_20d=9000, ma_50d=8000,
                                   avg_vol_20d=50000, bb_width_min_20d=500, prev_bb_width=600,
                                   w52_hgpr=12000, avg_trading_value_5d=10000000000)
    
    universe.get_watchlist.return_value = {"005930": item_kospi, "123456": item_kosdaq}
    
    # 마켓 타이밍: KOSPI False, KOSDAQ True
    async def mock_is_market_timing_ok(market):
        return market == "KOSDAQ"
    universe.is_market_timing_ok.side_effect = mock_is_market_timing_ok
    
    # 현재가 Mock (둘 다 돌파 조건 충족한다고 가정)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "999999", "acml_vol": "9999999", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    # KOSPI 종목은 제외되고 KOSDAQ 종목만 시그널 생성되어야 함
    assert len(signals) == 1
    assert signals[0].code == "123456"