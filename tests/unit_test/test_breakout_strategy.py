# tests/unit_test/strategies/oneil/test_breakout_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from common.types import ResCommonResponse, TradeSignal
from strategies.oneil.breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil.common_types import OSBWatchlistItem, OSBPositionState

@pytest.fixture
def mock_strategy_deps():
    ts = MagicMock()
    universe = MagicMock()
    tm = MagicMock()
    logger = MagicMock()
    
    ts.get_current_stock_price = AsyncMock()
    universe.get_watchlist = AsyncMock()
    universe.is_market_timing_ok = AsyncMock()
    
    return ts, universe, tm, logger

@pytest.mark.asyncio
async def test_scan_buy_signal(mock_strategy_deps):
    """scan: 돌파 조건 충족 시 매수 시그널 생성 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    
    # 1. 워치리스트 Mock (감시 대상 종목)
    item = OSBWatchlistItem(
        code="005930", name="Samsung", market="KOSPI",
        high_20d=70000, ma_20d=68000, ma_50d=65000,
        avg_vol_20d=100000, bb_width_min_20d=1000, prev_bb_width=1100,
        w52_hgpr=80000, avg_trading_value_5d=50000000000
    )
    universe.get_watchlist.return_value = {"005930": item}
    
    # 2. 마켓 타이밍 OK
    universe.is_market_timing_ok.return_value = True
    
    # 3. 장중 경과율 (50% 진행 가정)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    
    # 4. 현재가 Mock (돌파 성공 케이스)
    # 가격: 71000 (> 20일고가 70000)
    # 거래량: 200000 (환산 400000 > 평균 100000 * 1.5)
    # 프로그램 수급: 1000 (> 0)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="success", data={"output": {
            "stck_prpr": "71000", "acml_vol": "200000", "pgtr_ntby_qty": "1000"
        }}
    )
    
    signals = await strategy.scan()
    
    assert len(signals) == 1
    assert signals[0].code == "005930"
    assert signals[0].action == "BUY"
    assert signals[0].price == 71000
    # 내부 상태에 포지션 기록되었는지 확인
    assert "005930" in strategy._position_state

@pytest.mark.asyncio
async def test_check_exits_stop_loss(mock_strategy_deps):
    """check_exits: 손절 조건(-5%) 도달 시 매도 시그널 검증."""
    ts, universe, tm, logger = mock_strategy_deps
    strategy = OneilSqueezeBreakoutStrategy(ts, universe, tm, logger=logger)
    
    # 1. 보유 상태 설정
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10000, entry_date="20250101", peak_price=10000, breakout_level=9500
    )
    
    # 2. 현재가 Mock (손절가 도달: 9400원, -6%)
    ts.get_current_stock_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="success", data={"output": {"stck_prpr": "9400"}}
    )
    
    # 3. 보유 종목 리스트 전달
    holdings = [{"code": "005930", "buy_price": 10000}]
    
    signals = await strategy.check_exits(holdings)
    
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "손절" in signals[0].reason
    # 내부 상태에서 제거되었는지 확인
    assert "005930" not in strategy._position_state