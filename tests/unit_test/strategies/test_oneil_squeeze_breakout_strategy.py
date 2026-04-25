# tests/unit_test/test_oneil_squeeze_breakout_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from common.types import ResCommonResponse, TradeSignal
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_common_types import OSBWatchlistItem, OSBPositionState
from services.stock_query_service import StockQueryService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock

@pytest.fixture
def mock_strategy_deps():
    sqs = MagicMock(spec=StockQueryService)
    universe = MagicMock(spec=OneilUniverseService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()
    
    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    sqs.get_stock_conclusion = AsyncMock(spec=StockQueryService.get_stock_conclusion)
    sqs.get_recent_daily_ohlcv = AsyncMock(spec=StockQueryService.get_recent_daily_ohlcv)
    universe.get_watchlist = AsyncMock(spec=OneilUniverseService.get_watchlist)
    universe.is_market_timing_ok = AsyncMock(spec=OneilUniverseService.is_market_timing_ok)
    
    return sqs, universe, tm, logger

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

# _save_state_async 파일 I/O 차단 (파일 I/O 전용 TC 제외)
_FILE_IO_TESTS = {"test_load_save_state"}

@pytest.fixture(autouse=True)
def _block_async_file_io(monkeypatch, request):
    """check_exits에서 _save_state_async 호출 시 파일 I/O 방지.
    _load_state_async도 패치해 trailing stop 후 pop() 된 코드가 백그라운드 태스크에 의해 재로드되는 레이스 컨디션 방지.
    """
    if request.node.name in _FILE_IO_TESTS:
        yield
        return
    monkeypatch.setattr(OneilSqueezeBreakoutStrategy, "_save_state_async", AsyncMock())
    monkeypatch.setattr(OneilSqueezeBreakoutStrategy, "_load_state_async", AsyncMock())
    yield


@pytest.fixture
def scan_setup(mock_strategy_deps, breakout_candidate_item):
    """scan 메서드 테스트를 위한 공통 설정 픽스처."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    
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
    
    return strategy, sqs, universe, tm, logger

@pytest.mark.asyncio
async def test_scan_buy_signal(scan_setup):
    """scan: 돌파 조건 충족 시 매수 시그널 생성 검증."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 4. 현재가 Mock (돌파 성공 케이스)
    # 가격: 71000 (> 20일고가 70000)
    # 거래량: 200000 (환산 400000 > 평균 100000 * 1.5)
    # 프로그램 수급: 30000 (> 0)
    # 프로그램 금액: 30000 * 71000 = 21.3억
    # 거래대금: 142억 -> 21.3/142 = 15% (> 10%)
    # 시총: 1000억 -> 21.3/1000 = 2.13% (> 0.5%)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", 
            "stck_hgpr": "71100",  # 고가를 현재가와 가깝게 수정 (윗꼬리 축소)
            "stck_lwpr": "70500",  # 저가 유지
            "acml_vol": "200000", 
            "pgtr_ntby_qty": "30000",
            "acml_tr_pbmn": "14200000000"
        }}
    )

    # 5. 체결강도 Mock (150%) - 실제 API는 output이 배열, 필드명 tday_rltv
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].code == "005930"
    assert signals[0].action == "BUY"
    assert "강도 150.0%" in signals[0].reason
    assert signals[0].price == 71000
    # 내부 상태에 포지션 기록되었는지 확인
    assert "005930" in strategy._position_state

@pytest.mark.asyncio
async def test_scan_no_signal_if_price_not_breakout(scan_setup):
    """scan: 가격이 20일 고가를 돌파하지 못하면 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 가격 돌파 실패: 현재가 70000 <= 20일 고가 70000
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "70000", "acml_vol": "200000", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_buy_ratio_low(scan_setup):
    """scan: 프로그램 순매수 비중(거래대금 대비)이 낮으면 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 가격/거래량은 돌파, 순매수 수량도 양수지만 비중이 낮음
    # 순매수 1000주 * 71000 = 7100만원
    # 거래대금 142억 -> 0.5% (기준 10% 미달)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "1000", "acml_tr_pbmn": "14200000000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_market_cap_ratio_low(scan_setup):
    """scan: 프로그램 순매수 비중이 거래대금 조건은 만족하나 시가총액 조건 미달 시 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 시가총액: 1000억 (fixture 설정)
    # 프로그램 순매수: 4000주 * 71000원 = 2.84억
    #   -> 시총 대비: 2.84억 / 1000억 = 0.284% (< 0.3% 기준 미달)
    # 거래대금: 25억
    #   -> 거래대금 대비: 2.84억 / 25억 = 11.36% (> 10% 기준 만족)
    
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "4000", "acml_tr_pbmn": "2500000000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_candle_quality_poor(scan_setup):
    """scan: 캔들 품질이 기준 미달(윗꼬리가 너무 김) 시 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup

    # 돌파/거래량/수급 모두 만족하지만,
    # 고가 75000, 저가 65000 (변동폭 10000)
    # 현재가 71000
    # 상대 위치 = (71000 - 65000) / 10000 = 6000 / 10000 = 0.6 (< 0.7 기준 미달)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "30000",
            "acml_tr_pbmn": "14200000000",
            "stck_hgpr": "75000", "stck_lwpr": "65000"
        }}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_buy_signal_flexible_smart_money(scan_setup):
    """scan: 프로그램 순매수 비중이 10% 미달이더라도 유연화 조건(7% 이상 & 체결강도 140 이상) 만족 시 매수."""
    strategy, sqs, _, _, _ = scan_setup

    # 시가총액: 1000억
    # 프로그램 순매수: 12000주 * 71000원 = 8.52억
    # 거래대금: 100억
    # -> 거래대금 대비: 8.52 / 100 = 8.52% (10% 미달이지만 7% 이상 만족)
    # -> 시총 대비: 8.52 / 1000 = 0.852% (>= 0.3% * 0.7 = 0.21% 만족)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "12000",
            "acml_tr_pbmn": "10000000000",
            "stck_hgpr": "71500", "stck_lwpr": "69000"
        }}
    )
    
    # 체결강도 145 (> 140 유연화 기준)
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "145.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert "유연" in signals[0].reason

@pytest.mark.asyncio
async def test_scan_no_signal_if_execution_strength_low(scan_setup):
    """scan: 체결강도가 120 미만이면 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 모든 조건 통과, 하지만 체결강도 110.0 (< 120.0)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "30000",
            "acml_tr_pbmn": "14200000000"
        }}
    )
    
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "110.0"}]}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_early_market_volume_defense(scan_setup):
    """scan: 장 초반(09:00~09:20) 거래량 뻥튀기 방어 및 최소 거래량 조건 테스트."""
    strategy, sqs, _, tm, _ = scan_setup
    
    # 장 시작 16분 후 (09:16) -> progress ≈ 16/390 = 0.041
    # early morning guard(progress*390≥15)는 통과하되, effective_progress=max(0.041,0.05)=0.05 적용됨
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 16, 0)
    
    # 체결강도 Mock (150%) - 통과 조건
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )
    
    # 설정: 평균 거래량 100,000
    # Defense 2 기준: 30,000 (30%)
    
    # Case A: 거래량 부족 (20,000 < 30,000)
    # 프로그램 수급은 충분하게 설정 (10000주 = 7.1억)
    # 거래대금 14.2억 -> 50% (>10%)
    # 시총 1000억 -> 0.71% (>0.5%)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "20000", "pgtr_ntby_qty": "10000", "acml_tr_pbmn": "1420000000"
        }}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 0 # Defense 2에 의해 탈락

    # Case B: 거래량 충분 (40,000 > 30,000)
    # Defense 1 적용: 40,000 / 0.05 = 800,000 (> 150,000). 통과.
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000",
            "stck_hgpr": "71100",  # 고가 추가
            "stck_lwpr": "70500",  # 저가 추가
            "acml_vol": "40000",
            "pgtr_ntby_qty": "10000",
            "acml_tr_pbmn": "2840000000"
        }}
    )
    
    signals = await strategy.scan()
    assert len(signals) == 1 # 통과

@pytest.mark.asyncio
async def test_scan_no_signal_if_volume_not_breakout(scan_setup):
    """scan: 예상 거래량이 기준 미달이면 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 거래량 돌파 실패: 환산 거래량 100000 < 100000 * 1.5
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "50000", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_scan_no_signal_if_program_buy_low(scan_setup):
    """scan: 프로그램 순매수가 기준 미달이면 매수 시그널 없음."""
    strategy, sqs, _, _, _ = scan_setup
    
    # 프로그램 수급 실패: 순매수 0 <= 기준 0
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "0"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_check_exits_stop_loss(mock_strategy_deps):
    """check_exits: 손절 조건(-5%) 도달 시 매도 시그널 검증."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (손절가 도달: 9400원, -6%)
    sqs.get_current_price.return_value = ResCommonResponse(
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
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정 (매수가 10000, 최고가 12000)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=12000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (트레일링 스탑 발동: 10600원, 최고가 12000 대비 -11.7%)
    # pnl = (10600-10000)/10000 = 6% < 7% → 조기 부분익절 미발동 → 트레일링스탑 도달
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10600"}}
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
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정 (최고가 11000, last_partial_sell_price=12000 → ref_price=12000)
    # ref_price=12000 → pnl_from_ref = (12500-12000)/12000 = 4.2% < 7% → 부분익절 미발동
    state = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=11000, breakout_level=9500
    )
    state.last_partial_sell_price = 12000
    strategy._position_state["005930"] = state

    # 2. 현재가 Mock (신고가 12500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "12500"}}
    )

    # 3. 보유 종목 리스트 전달
    holdings = [{"code": "005930", "buy_price": 10000}]

    signals = await strategy.check_exits(holdings)

    # 매도 시그널은 없어야 함
    assert len(signals) == 0
    # 최고가가 12500으로 갱신되었는지 확인
    assert strategy._position_state["005930"].peak_price == 12500
    
@pytest.mark.asyncio
async def test_check_exits_signal_name_fallback(mock_strategy_deps):
    """매도 시그널 생성 시 holdings의 name이 TradeSignal에 정상 반영되는지 검증."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9500)
    
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9400"}})
    holdings = [{"code": "005930", "buy_price": 10000, "name": "기존이름"}]
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].name == "기존이름"

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
    strategy, sqs, _, _, _ = scan_setup
    # 005930을 보유 상태로 설정
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9000)
    
    # API 호출이 발생하지 않아야 함 (mock reset)
    sqs.get_current_price.reset_mock()
    
    signals = await strategy.scan()
    assert len(signals) == 0
    sqs.get_current_price.assert_not_called()

@pytest.mark.asyncio
async def test_scan_bad_market_timing(scan_setup):
    """scan: 마켓 타이밍이 좋지 않으면 스캔 제외."""
    strategy, sqs, universe, _, _ = scan_setup
    universe.is_market_timing_ok.return_value = False
    
    signals = await strategy.scan()
    assert len(signals) == 0
    # 마켓 타이밍 체크 후 탈락하므로 가격 조회 API 호출 안함
    sqs.get_current_price.assert_not_called()

@pytest.mark.asyncio
async def test_scan_exception_handling(scan_setup):
    """scan: 개별 종목 처리 중 예외 발생 시 로그 남기고 계속 진행."""
    strategy, sqs, _, _, logger = scan_setup
    # check_breakout 내부에서 호출되는 API가 예외 발생
    sqs.get_current_price.side_effect = Exception("API Error")
    
    signals = await strategy.scan()
    assert len(signals) == 0
    logger.error.assert_called()

@pytest.mark.asyncio
async def test_check_breakout_api_failure(scan_setup):
    """_check_breakout: 현재가 조회 실패 시 None 반환."""
    strategy, sqs, _, _, _ = scan_setup
    # API 실패 응답
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    
    signals = await strategy.scan()
    assert len(signals) == 0

@pytest.mark.asyncio
async def test_check_exits_api_failure(mock_strategy_deps):
    """check_exits: 현재가 조회 실패 시 시그널 생성 안함."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9000)
    
    # API 실패
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="1", msg1="Fail")
    
    holdings = [{"code": "005930", "buy_price": 10000}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 0

def test_calculate_qty_zero_price(mock_strategy_deps):
    """_calculate_qty: 가격이 0 이하일 때 1 반환."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    assert strategy._calculate_qty(0) == 1
    assert strategy._calculate_qty(-100) == 1

def test_load_save_state(mock_strategy_deps, tmp_path):
    """_load_state, _save_state: 파일 입출력 동작 검증."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    
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
    strategy2 = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy2.STATE_FILE = str(test_file)
    strategy2._position_state = {} # 초기화
    strategy2._load_state()
    
    assert "005930" in strategy2._position_state
    assert strategy2._position_state["005930"].peak_price == 11000

def test_check_time_stop_logic(mock_strategy_deps):
    """_check_time_stop: 시간 손절 로직 상세 검증 (OHLCV를 직접 전달하는 동기 메서드)."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    # 설정: 5일 경과, 박스권 2%
    strategy._cfg.time_stop_days = 5
    strategy._cfg.time_stop_box_range_pct = 2.0

    tm.get_current_kst_time.return_value = datetime(2025, 1, 10, 12, 0, 0)

    state = OSBPositionState(entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=10000)

    # 진입일(0101) 이후 5거래일치 데이터 (0102~0106)
    ohlcv = [
        {"date": "20250101"},  # 진입일
        {"date": "20250102"}, {"date": "20250103"}, {"date": "20250104"},
        {"date": "20250105"}, {"date": "20250106"},  # 5일 경과
    ]

    # Case 1: 5일 경과 & 횡보(10100원, +1%) & 급등이력 없음 -> True (손절)
    assert strategy._check_time_stop(state, 10100, ohlcv) is True

    # Case 2: 5일 경과 & 상승 이탈(10300원, +3%) -> False
    assert strategy._check_time_stop(state, 10300, ohlcv) is False

    # Case 3: 5일 경과 & 하락 이탈(9700원, -3%) -> True (상승 미달로 손절)
    assert strategy._check_time_stop(state, 9700, ohlcv) is True

    # Case 4: 5일 경과 & 횡보(10100원) & 급등이력 있음(peak 10600원, +6%) -> False
    state_peak = OSBPositionState(entry_price=10000, entry_date="20250101", peak_price=10600, breakout_level=10000)
    assert strategy._check_time_stop(state_peak, 10100, ohlcv) is False

    # Case 5: 4일 경과 (데이터 부족) -> False
    ohlcv_short = ohlcv[:-1]  # 4일치
    assert strategy._check_time_stop(state, 10100, ohlcv_short) is False

@pytest.mark.asyncio
async def test_check_exits_trend_break(mock_strategy_deps):
    """check_exits: 추세 이탈(10MA 하향 + 대량 거래량) 시 매도 시그널 검증."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    # 1. 보유 상태 설정
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10500, breakout_level=10000
    )
    
    # 2. OHLCV Mock (10MA, 20AvgVol 계산용)
    # 20일치 데이터. 종가 11000, 거래량 100000으로 일정하다고 가정.
    # 10MA = 11000, 20AvgVol = 100000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(20)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # 3. 현재가 Mock (추세 이탈 조건)
    # 가격: 10500 (< 10MA 11000) → 가격 이탈, pnl=5% < 7% → 조기 부분익절 미발동
    # 거래량: 60000. 장 진행률 0.5 가정 -> 예상 120000 (> 100000) -> 거래량 이탈
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "60000"}}
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
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10500, breakout_level=10000
    )
    
    # 10MA = 11000, 20AvgVol = 100000
    ohlcv = [{"close": 11000, "volume": 100000} for _ in range(20)]
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=ohlcv)
    
    # 가격: 10500 (< 11000) → 가격 이탈, pnl=5% < 7% → 조기 부분익절 미발동
    # 거래량: 40000. 장 진행률 0.5 -> 예상 80000 (< 100000) -> 거래량 부족
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "40000"}}
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
    strategy, sqs, universe, _, _ = scan_setup

    # 워치리스트: KOSPI 종목 1개, KOSDAQ 종목 1개
    item_kospi = OSBWatchlistItem(code="005930", name="Samsung", market="KOSPI",
                                    high_20d=70000, ma_20d=68000, ma_50d=65000,
                                    avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
                                    w52_hgpr=80000, avg_trading_value_5d=50000000000,
                                    market_cap=400_000_000_000_000)
    item_kosdaq = OSBWatchlistItem(code="123456", name="KOSDAQ_Stock", market="KOSDAQ",
                                    high_20d=10000, ma_20d=9000, ma_50d=8000,
                                    avg_vol_20d=50000, bb_width_min_20d=500, prev_bb_width=600,
                                    w52_hgpr=12000, avg_trading_value_5d=10000000000,
                                    market_cap=10_000_000_000_000)

    universe.get_watchlist.return_value = {"005930": item_kospi, "123456": item_kosdaq}

    # 마켓 타이밍: KOSPI False, KOSDAQ True
    async def mock_is_market_timing_ok(market, caller=None, logger=None):
        return market == "KOSDAQ"
    universe.is_market_timing_ok.side_effect = mock_is_market_timing_ok

    # KOSDAQ 종목(123456)만 마켓 타이밍 통과 → KOSDAQ high_20d=10000 기준으로 가격 설정
    # 10100: high_20d=10000 초과(돌파) & 과확장 가드(10000*1.02=10200) 통과
    # pg_buy_amount = 5000000 * 10100 = 505억, market_cap = 10조 → 0.505% ≥ 0.5% ✓
    # pg_to_tv = 505억 / 5000억 = 10.1% ≥ 10% ✓
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "10100",
            "stck_hgpr": "10150",
            "stck_lwpr": "9950",
            "acml_vol": "50000",
            "pgtr_ntby_qty": "5000000",
            "acml_tr_pbmn": "500000000000"
        }}
    )

    # 체결강도 Mock (150%) - 통과 조건
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    # KOSPI 종목은 제외되고 KOSDAQ 종목만 시그널 생성되어야 함
    assert len(signals) == 1
    assert signals[0].code == "123456"


# ============================================================================
# 성능 개선 검증: OHLCV 중복 호출 제거 + Dirty Flag
# ============================================================================

@pytest.mark.asyncio
async def test_check_exits_ohlcv_fetched_once_per_holding(mock_strategy_deps):
    """check_exits: _check_time_stop과 _check_trend_break가 OHLCV를 공유 — 보유 1종목당 1회 조회."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()

    # 손절/트레일링스탑 조건을 모두 피하도록 설정 (pnl +10%, peak 동일)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=11000, breakout_level=9500
    )

    # 현재가 10600 → pnl +6% < 7% (조기 부분익절 미발동), peak(11000) 동일 → trailing stop 없음
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10600", "acml_vol": "30000"}}
    )
    # OHLCV: 시간손절·추세이탈 조건 미달 (데이터 부족)
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])

    tm.get_current_kst_time.return_value = datetime(2025, 1, 10, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 10, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 10, 15, 30, 0)

    await strategy.check_exits([{"code": "005930", "buy_price": 10000}])

    # OHLCV는 1회만 조회 (time_stop + trend_break 공유)
    assert sqs.get_recent_daily_ohlcv.call_count == 1


@pytest.mark.asyncio
async def test_check_exits_dirty_flag_saves_once(mock_strategy_deps):
    """check_exits: 여러 보유종목 최고가 갱신 시 _save_state는 루프 후 1회만 호출."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state = MagicMock()
    strategy._save_state_async = AsyncMock()

    # 3개 종목 보유 — 모두 최고가 갱신 대상 (peak 10000 < current 11000)
    for code in ["005930", "000660", "035420"]:
        strategy._position_state[code] = OSBPositionState(
            entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=9500
        )

    # 현재가 10500 → pnl +5% < 7% (조기 부분익절 미발동), peak(10000) 갱신, 청산 조건 미달
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10500", "acml_vol": "30000"}}
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])

    tm.get_current_kst_time.return_value = datetime(2025, 1, 10, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 10, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 10, 15, 30, 0)

    holdings = [
        {"code": "005930", "buy_price": 10000},
        {"code": "000660", "buy_price": 10000},
        {"code": "035420", "buy_price": 10000},
    ]
    signals = await strategy.check_exits(holdings)

    # 매도 시그널 없음, 3종목 모두 peak 갱신
    assert signals == []
    for code in ["005930", "000660", "035420"]:
        assert strategy._position_state[code].peak_price == 10500
    # dirty flag: check_exits는 _save_state_async를 1회만 호출
    strategy._save_state_async.assert_called_once()


@pytest.mark.asyncio
async def test_scan_parallel_exception_in_one_does_not_block_others(mock_strategy_deps):
    """scan: asyncio.gather — 한 종목 처리 중 예외 발생해도 나머지 종목 시그널은 정상 생성."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()

    # 2개 종목: 000001은 예외, 000002는 돌파 성공
    item_fail = OSBWatchlistItem(
        code="000001", name="예외종목", market="KOSPI",
        high_20d=10000, ma_20d=9000, ma_50d=8000,
        avg_vol_20d=100000, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=12000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
    )
    item_ok = OSBWatchlistItem(
        code="000002", name="정상종목", market="KOSPI",
        high_20d=10000, ma_20d=9000, ma_50d=8000,
        avg_vol_20d=100000, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=12000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
    )
    universe.get_watchlist.return_value = {"000001": item_fail, "000002": item_ok}
    universe.is_market_timing_ok.return_value = True

    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    # 000001은 예외, 000002는 모든 관문 통과
    async def price_side_effect(code, caller=None):
        if code == "000001":
            raise Exception("API 타임아웃")
        # 000002: 가격돌파(10100 > 10000), 과확장 가드 통과(10100 ≤ 10000*1.02=10200)
        # 거래량 돌파, 스마트머니 통과
        # pg_buy_amount = 200000 * 10100 = 20.2억 / 142억 = 14.2% > 10% ✓
        # pg_to_mc_pct = 20.2억 / 1000억 = 2.02% > 0.5% ✓
        return ResCommonResponse(
            rt_cd="0", msg1="OK", data={"output": {
                "stck_prpr": "10100", "acml_vol": "200000",
                "pgtr_ntby_qty": "200000", "acml_tr_pbmn": "14200000000",
            }}
        )

    sqs.get_current_price.side_effect = price_side_effect
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    # 예외 로그 기록
    logger.error.assert_called()
    # 정상 종목은 시그널 생성
    assert len(signals) == 1
    assert signals[0].code == "000002"


@pytest.mark.asyncio
async def test_scan_skip_cooldown_candidate(scan_setup):
    """scan: 쿨다운 해제일 전에는 후보에서 제외한다."""
    strategy, sqs, _, tm, _ = scan_setup
    strategy._cooldown["005930"] = "20250102"
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    signals = await strategy.scan()

    assert signals == []
    sqs.get_current_price.assert_not_called()


@pytest.mark.asyncio
async def test_scan_early_morning_guard_blocks_breakout(scan_setup):
    """scan: 장 시작 15분 이내에는 돌파 후보라도 매수하지 않는다."""
    strategy, sqs, _, tm, logger = scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 9, 10, 0)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000",
            "stck_hgpr": "71100",
            "stck_lwpr": "70500",
            "acml_vol": "40000",
            "pgtr_ntby_qty": "10000",
            "acml_tr_pbmn": "2840000000",
        }}
    )

    signals = await strategy.scan()

    assert signals == []
    logger.debug.assert_called()
    sqs.get_stock_conclusion.assert_not_called()


@pytest.mark.asyncio
async def test_scan_no_signal_if_over_extended(scan_setup):
    """scan: 20일 고가 대비 과확장되면 추격 매수하지 않는다."""
    strategy, sqs, _, _, logger = scan_setup
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "72000",
            "stck_hgpr": "72100",
            "stck_lwpr": "71500",
            "acml_vol": "200000",
            "pgtr_ntby_qty": "30000",
            "acml_tr_pbmn": "14200000000",
        }}
    )

    signals = await strategy.scan()

    assert signals == []
    logger.debug.assert_called()
    sqs.get_stock_conclusion.assert_not_called()


@pytest.mark.asyncio
async def test_scan_no_signal_if_price_output_missing(scan_setup):
    """scan: 현재가 응답에 output이 없으면 조용히 스킵한다."""
    strategy, sqs, _, _, _ = scan_setup
    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={})

    signals = await strategy.scan()

    assert signals == []


@pytest.mark.asyncio
async def test_scan_rejects_when_execution_strength_lookup_fails(scan_setup):
    """scan: 체결강도 조회 실패 시 0으로 간주되어 매수하지 않는다."""
    strategy, sqs, _, _, logger = scan_setup
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000",
            "stck_hgpr": "71100",
            "stck_lwpr": "70500",
            "acml_vol": "200000",
            "pgtr_ntby_qty": "30000",
            "acml_tr_pbmn": "14200000000",
        }}
    )
    sqs.get_stock_conclusion.side_effect = Exception("conclusion timeout")

    signals = await strategy.scan()

    assert signals == []
    logger.warning.assert_called()


def test_is_smart_money_ok_dynamic_market_cap_thresholds(mock_strategy_deps):
    """_is_smart_money_ok: 대형/중형 시총별 가변 허들을 적용한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    large_ok, large_metrics = strategy._is_smart_money_ok(
        "BIG", current=10000, pg_buy=1_000_000, trade_value=50_000_000_000,
        market_cap=10_000_000_000_000, cgld_val=150.0,
    )
    mid_ok, mid_metrics = strategy._is_smart_money_ok(
        "MID", current=10000, pg_buy=200_000, trade_value=10_000_000_000,
        market_cap=1_000_000_000_000, cgld_val=150.0,
    )
    zero_denominator_ok, zero_metrics = strategy._is_smart_money_ok(
        "ZERO", current=10000, pg_buy=1, trade_value=0,
        market_cap=0, cgld_val=150.0,
    )

    assert large_ok is True
    assert large_metrics["mc_threshold"] == 0.1
    assert mid_ok is True
    assert mid_metrics["mc_threshold"] == 0.2
    assert zero_denominator_ok is False
    assert zero_metrics["pg_to_tv_pct"] == 0
    assert zero_metrics["pg_to_mc_pct"] == 0


def test_check_trend_break_guard_paths(mock_strategy_deps):
    """_check_trend_break: 데이터 부족/10MA 미이탈 경로를 검증한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    assert strategy._check_trend_break("005930", 10000, 1000, []) == (False, "")
    ohlcv = [{"close": 10000, "volume": 100000} for _ in range(20)]
    assert strategy._check_trend_break("005930", 10000, 1000, ohlcv) == (False, "")


@pytest.mark.asyncio
async def test_check_exits_empty_and_invalid_holding(mock_strategy_deps):
    """check_exits: 빈 보유 목록과 필수 필드 누락 보유 항목을 스킵한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    assert await strategy.check_exits([]) == []
    assert await strategy.check_exits([{"code": "005930"}]) == []
    sqs.get_current_price.assert_not_called()


@pytest.mark.asyncio
async def test_check_exits_partial_profit_updates_state(mock_strategy_deps):
    """check_exits: 조기 부분익절 시 일부 수량 매도와 본절스탑 무장을 기록한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._save_state_async = AsyncMock()
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10800", "acml_vol": "10000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 10}])

    assert len(signals) == 1
    assert signals[0].qty == 3
    assert "조기부분익절" in signals[0].reason
    state = strategy._position_state["005930"]
    assert state.last_partial_sell_price == 10800
    assert state.breakeven_armed is True
    strategy._save_state_async.assert_called_once()


@pytest.mark.asyncio
async def test_check_exits_partial_profit_full_quantity(mock_strategy_deps):
    """check_exits: 부분익절 계산 수량이 보유 수량 이상이면 전량익절로 표시한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10800", "acml_vol": "10000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 1}])

    assert len(signals) == 1
    assert signals[0].qty == 1
    assert "전량익절" in signals[0].reason


@pytest.mark.asyncio
async def test_check_exits_breakeven_stop_after_partial_profit(mock_strategy_deps):
    """check_exits: 부분익절 후 진입가를 하회하면 본절스탑 매도 신호를 낸다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    state = OSBPositionState(10000, "20250101", 10000, 9500)
    state.breakeven_armed = True
    state.last_partial_sell_price = 10800
    strategy._position_state["005930"] = state
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "9900", "acml_vol": "10000"}}
    )

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 5}])

    assert len(signals) == 1
    assert signals[0].qty == 5
    assert "본절스탑" in signals[0].reason
    assert "005930" not in strategy._position_state
    assert strategy._cooldown["005930"] >= datetime.today().strftime("%Y%m%d")


@pytest.mark.asyncio
async def test_check_exits_time_stop_signal(mock_strategy_deps):
    """check_exits: N거래일 횡보 조건이면 시간손절 신호를 낸다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._cfg.time_stop_days = 5
    strategy._cfg.time_stop_box_range_pct = 2.0
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10100", "acml_vol": "10000"}}
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data=[
            {"date": "20250101"},
            {"date": "20250102"},
            {"date": "20250103"},
            {"date": "20250104"},
            {"date": "20250105"},
            {"date": "20250106"},
        ],
    )
    tm.get_current_kst_time.return_value = datetime(2025, 1, 10, 12, 0, 0)

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000, "qty": 5}])

    assert len(signals) == 1
    assert signals[0].qty == 5
    assert "시간손절" in signals[0].reason


def test_init_uses_default_strategy_logger(mock_strategy_deps, monkeypatch):
    """__init__: logger 미주입 시 전략 로거를 생성한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    created_logger = MagicMock()
    get_logger = MagicMock(return_value=created_logger)
    monkeypatch.setattr(
        "strategies.oneil_squeeze_breakout_strategy.get_strategy_logger",
        get_logger,
    )

    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm)

    get_logger.assert_called_once_with("OneilSqueezeBreakout")
    assert strategy._logger is created_logger


def test_is_smart_money_rejects_non_positive_program_buy(mock_strategy_deps):
    """_is_smart_money_ok: 프로그램 순매수가 0 이하이면 즉시 실패한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    ok, metrics = strategy._is_smart_money_ok(
        "005930", current=10000, pg_buy=0, trade_value=1_000_000,
        market_cap=100_000_000_000, cgld_val=150.0,
    )

    assert ok is False
    assert metrics == {}


@pytest.mark.asyncio
async def test_scan_no_signal_if_smart_money_rejects_after_execution_strength(scan_setup):
    """scan: 체결강도 통과 후 스마트머니 조건만 실패하면 매수하지 않는다."""
    strategy, sqs, _, _, _ = scan_setup
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {
            "stck_prpr": "71000",
            "stck_hgpr": "71100",
            "stck_lwpr": "70500",
            "acml_vol": "200000",
            "pgtr_ntby_qty": "1000",
            "acml_tr_pbmn": "14200000000",
        }}
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    assert signals == []


@pytest.mark.asyncio
async def test_check_exits_logs_exception_from_single_exit(mock_strategy_deps):
    """check_exits: 개별 청산 검사 예외를 로깅하고 빈 결과를 반환한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._check_single_exit = AsyncMock(side_effect=Exception("exit boom"))

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])

    assert signals == []
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_check_exits_creates_state_when_missing(mock_strategy_deps):
    """_check_single_exit: 내부 상태가 없으면 매수가 기준 상태를 생성한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10000", "acml_vol": "10000"}}
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])

    assert signals == []
    assert strategy._position_state["005930"].entry_price == 10000


@pytest.mark.asyncio
async def test_check_exits_returns_empty_on_missing_output_and_zero_price(mock_strategy_deps):
    """_check_single_exit: output 누락/현재가 0 방어 경로를 검증한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 10000, 9500)

    sqs.get_current_price.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data={})
    assert await strategy.check_exits([{"code": "005930", "buy_price": 10000}]) == []

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "0", "acml_vol": "10000"}}
    )
    assert await strategy.check_exits([{"code": "005930", "buy_price": 10000}]) == []


@pytest.mark.asyncio
async def test_check_exits_skips_trailing_when_peak_price_zero(mock_strategy_deps):
    """_check_single_exit: peak_price가 0이면 트레일링스탑 평가를 건너뛴다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state["005930"] = OSBPositionState(10000, "20250101", 0, 9500)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "10100", "acml_vol": "10000"}}
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])

    signals = await strategy.check_exits([{"code": "005930", "buy_price": 10000}])

    assert signals == []


def test_check_time_stop_invalid_state_returns_false(mock_strategy_deps):
    """_check_time_stop: 진입일/진입가가 유효하지 않으면 False를 반환한다."""
    sqs, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)

    assert strategy._check_time_stop(OSBPositionState(0, "", 0, 0), 10000, [{"date": "20250102"}]) is False
