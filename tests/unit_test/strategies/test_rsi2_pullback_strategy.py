# tests/unit_test/strategies/test_rsi2_pullback_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from common.types import ResCommonResponse, TradeSignal
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
from strategies.rsi2_pullback_types import RSI2PullbackConfig
from strategies.oneil_common_types import OSBWatchlistItem
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.oneil_universe_service import OneilUniverseService
from core.market_clock import MarketClock


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _price_resp(current="10000"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_prpr": current}},
    )


def _rsi_resp(value):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"code": "005930", "date": "20250101", "close": 10000.0, "rsi": value}],
    )


def _ma_resp(value):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"code": "005930", "date": "20250101", "close": 10000.0, "ma": value}],
    )


# ── 공통 Fixture ──────────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    sqs = MagicMock(spec=StockQueryService)
    universe = MagicMock(spec=OneilUniverseService)
    indicator = MagicMock(spec=IndicatorService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()

    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    universe.get_watchlist = AsyncMock(spec=OneilUniverseService.get_watchlist)
    universe.is_market_timing_ok = AsyncMock(spec=OneilUniverseService.is_market_timing_ok)
    indicator.get_rsi = AsyncMock(spec=IndicatorService.get_rsi)
    indicator.get_moving_average = AsyncMock(spec=IndicatorService.get_moving_average)

    return sqs, universe, indicator, tm, logger


@pytest.fixture
def watchlist_item_stage2():
    return OSBWatchlistItem(
        code="005930", name="삼성전자", market="KOSPI",
        high_20d=12000, ma_20d=10500, ma_50d=10000,
        avg_vol_20d=100000, bb_width_min_20d=500, prev_bb_width=600,
        w52_hgpr=15000, avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
        ma_200d=9500.0, minervini_stage=2,
    )


@pytest.fixture
def scan_setup(mock_deps, watchlist_item_stage2):
    """모든 진입 조건이 통과되도록 구성된 scan() 셋업."""
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger)
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    strategy._load_state = MagicMock()

    universe.get_watchlist.return_value = {"005930": watchlist_item_stage2}
    universe.is_market_timing_ok.return_value = True
    indicator.get_rsi.return_value = _rsi_resp(8.0)  # ≤ 10
    sqs.get_current_price.return_value = _price_resp("10000")

    # 15:15 (cutoff 15:10 통과)
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)

    return strategy, sqs, universe, indicator, tm, logger


# ── scan() 테스트 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_emits_buy_signal_when_all_conditions_met(scan_setup):
    """Stage 2 + RSI(2) ≤ 10 + 15:10 이후 → BUY 시그널 1건 발행."""
    strategy, _, _, _, _, _ = scan_setup
    signals = await strategy.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, TradeSignal)
    assert sig.action == "BUY"
    assert sig.code == "005930"
    assert sig.strategy_name == "RSI2눌림목"
    assert "RSI" in sig.reason


@pytest.mark.asyncio
async def test_scan_skips_before_cutoff_time(scan_setup):
    """15:10 이전이면 종가 베팅 스캔을 건너뛴다."""
    strategy, _, _, _, tm, _ = scan_setup
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 14, 0, 0)
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_non_stage2_candidate(scan_setup, watchlist_item_stage2):
    """Stage 2가 아닌 종목은 RSI 조건과 무관하게 제외."""
    strategy, _, universe, _, _, _ = scan_setup
    not_stage2 = watchlist_item_stage2
    not_stage2.minervini_stage = 3
    universe.get_watchlist.return_value = {"005930": not_stage2}
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_filters_high_rsi(scan_setup):
    """RSI(2) > 10이면 진입하지 않음."""
    strategy, _, _, indicator, _, _ = scan_setup
    indicator.get_rsi.return_value = _rsi_resp(15.0)
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_in_cooldown(scan_setup):
    """쿨다운 기간 내 종목은 재진입 차단."""
    strategy, _, _, _, _, _ = scan_setup
    strategy._cooldown = {"005930": "29991231"}
    signals = await strategy.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_market_timing_off_marks_risk_off(scan_setup):
    """지수 마켓 타이밍 🔴이면 risk_off 플래그가 True로 등록되고 신호 reason에 '축소비중'."""
    strategy, _, universe, _, _, _ = scan_setup
    universe.is_market_timing_ok.return_value = False
    signals = await strategy.scan()
    assert len(signals) == 1
    assert "축소비중" in signals[0].reason
    state = strategy._position_state["005930"]
    assert state.risk_off_entry is True


# ── check_exits() 테스트 ─────────────────────────────────────────

@pytest.fixture
def exit_setup(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger)
    strategy._position_state = {}
    strategy._cooldown = {}
    strategy._save_state = MagicMock()
    return strategy, sqs, indicator


@pytest.mark.asyncio
async def test_check_exits_take_profit_on_5ma_touch(exit_setup):
    """현재가 ≥ 5MA → 5MA 터치 익절 SELL."""
    strategy, sqs, indicator = exit_setup
    sqs.get_current_price.return_value = _price_resp("10500")
    indicator.get_moving_average.side_effect = [
        _ma_resp(9000.0),   # 200MA (current 10500 > 9000 → 추세 OK)
        _ma_resp(10400.0),  # 5MA (current 10500 >= 10400 → 익절)
    ]
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 10000, "qty": 5}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "5MA 터치" in signals[0].reason
    assert signals[0].qty == 5


@pytest.mark.asyncio
async def test_check_exits_hard_stop_at_minus5pct(exit_setup):
    """진입가 대비 -5% 도달 → 하드 스탑 SELL."""
    strategy, sqs, indicator = exit_setup
    sqs.get_current_price.return_value = _price_resp("9500")  # -5%
    indicator.get_moving_average.side_effect = [
        _ma_resp(9000.0),   # 200MA OK (9500 > 9000)
        _ma_resp(11000.0),  # 5MA 미도달 (9500 < 11000)
    ]
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 10000, "qty": 5}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "하드 스탑" in signals[0].reason


@pytest.mark.asyncio
async def test_check_exits_trend_break_below_200ma(exit_setup):
    """현재가 < 200MA → 추세 붕괴 손절 SELL (하드 스탑보다 우선)."""
    strategy, sqs, indicator = exit_setup
    sqs.get_current_price.return_value = _price_resp("9700")  # -3% 손익
    indicator.get_moving_average.side_effect = [
        _ma_resp(9800.0),   # 200MA (9700 < 9800 → 추세 붕괴)
        _ma_resp(11000.0),  # 5MA 미도달
    ]
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 10000, "qty": 5}]
    signals = await strategy.check_exits(holdings)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "추세 붕괴" in signals[0].reason
    # 손절성 청산이므로 쿨다운에 등록
    assert "005930" in strategy._cooldown


@pytest.mark.asyncio
async def test_check_exits_no_signal_when_in_range(exit_setup):
    """200MA 위 + -5% 미만 손익 + 5MA 미도달 → 신호 없음."""
    strategy, sqs, indicator = exit_setup
    sqs.get_current_price.return_value = _price_resp("9800")  # -2%
    indicator.get_moving_average.side_effect = [
        _ma_resp(9500.0),   # 200MA OK (9800 > 9500)
        _ma_resp(10500.0),  # 5MA 미도달 (9800 < 10500)
    ]
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 10000, "qty": 5}]
    signals = await strategy.check_exits(holdings)
    assert signals == []


# ── 인터페이스 ────────────────────────────────────────────────────

def test_name_property(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger)
    assert strategy.name == "RSI2눌림목"
