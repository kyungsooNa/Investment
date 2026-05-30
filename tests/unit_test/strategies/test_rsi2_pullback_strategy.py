# tests/unit_test/strategies/test_rsi2_pullback_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from types import SimpleNamespace

from common.types import ResCommonResponse, TradeSignal
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
from strategies.rsi2_pullback_types import RSI2PullbackConfig, RSI2PositionState
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
async def test_scan_prefetches_candidate_prices_in_batch(scan_setup):
    """P2 2-5: scan 은 종목당 개별 현재가 조회 전에 후보군을 batch prefetch 한다."""
    strategy, sqs, _, _, _, _ = scan_setup
    await strategy.scan()
    sqs.prefetch_prices.assert_awaited_once_with(["005930"])


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


@pytest.mark.asyncio
async def test_scan_skips_empty_watchlist(scan_setup):
    strategy, _, universe, _, _, _ = scan_setup
    universe.get_watchlist.return_value = {}

    signals = await strategy.scan()

    assert signals == []


@pytest.mark.asyncio
async def test_scan_logs_entry_check_exception(scan_setup):
    strategy, _, _, indicator, _, logger = scan_setup
    indicator.get_rsi.side_effect = RuntimeError("rsi down")

    signals = await strategy.scan()

    assert signals == []
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_entry_skips_bad_rsi_current_price_and_zero_qty(scan_setup, watchlist_item_stage2):
    strategy, sqs, _, indicator, _, _ = scan_setup

    indicator.get_rsi.return_value = ResCommonResponse(rt_cd="1", msg1="fail", data=None)
    assert await strategy._check_entry("005930", watchlist_item_stage2, {"KOSPI": True}) is None

    indicator.get_rsi.return_value = _rsi_resp(8.0)
    sqs.get_current_price.return_value = _price_resp("0")
    assert await strategy._check_entry("005930", watchlist_item_stage2, {"KOSPI": True}) is None

    sqs.get_current_price.return_value = _price_resp("10000")
    strategy._calculate_qty = MagicMock(return_value=0)
    assert await strategy._check_entry("005930", watchlist_item_stage2, {"KOSPI": True}) is None


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
async def test_check_exits_hard_stop_net_triggers_at_gross_below_threshold(exit_setup):
    """P0 0-9: gross -4.8% 는 hard_stop_pct=-5% 미달이지만 net 은 -5.02% → 손절 발동.

    backtest 와 동일하게 net 기준으로 trigger 가 평가됨을 회귀 방지로 고정.
    """
    strategy, sqs, indicator = exit_setup
    # buy=10000, sell=9520 → gross -4.80%, net ≈ -5.02% (수수료+세금 ≈ 0.22% drag)
    sqs.get_current_price.return_value = _price_resp("9520")
    indicator.get_moving_average.side_effect = [
        _ma_resp(9000.0),   # 200MA OK (9520 > 9000 → 추세 붕괴 트리거 아님)
        _ma_resp(11000.0),  # 5MA 미도달
    ]
    holdings = [{"code": "005930", "name": "삼성전자", "buy_price": 10000, "qty": 5}]
    signals = await strategy.check_exits(holdings)
    # net 기준 -5.02% ≤ -5 → 하드 스탑 발동. gross 기준이었다면 -4.8% > -5 로 미발동.
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "하드 스탑" in signals[0].reason
    assert "net" in signals[0].reason.lower()


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


@pytest.mark.asyncio
async def test_check_exits_empty_and_bad_holdings_are_ignored(exit_setup):
    strategy, sqs, _ = exit_setup

    assert await strategy.check_exits([]) == []
    assert await strategy._check_single_exit({}) is None

    sqs.get_current_price.return_value = _price_resp("0")
    assert await strategy._check_single_exit({"code": "005930"}) is None


@pytest.mark.asyncio
async def test_check_exits_logs_exceptions_and_ignores_none_results(exit_setup):
    strategy, _, _ = exit_setup
    strategy._check_single_exit = AsyncMock(side_effect=[RuntimeError("exit down"), None])

    signals = await strategy.check_exits([{"code": "005930"}, {"code": "000660"}])

    assert signals == []
    strategy._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_check_single_exit_defaults_buy_price_to_current(exit_setup):
    strategy, sqs, indicator = exit_setup
    sqs.get_current_price.return_value = _price_resp("10500")
    indicator.get_moving_average.side_effect = [
        _ma_resp(9000.0),
        _ma_resp(10400.0),
    ]

    result = await strategy._check_single_exit({"code": "005930", "buy_price": 0})

    signal, dirty = result
    assert signal.price == 10500
    assert dirty is False


def test_extract_current_price_handles_invalid_responses_and_objects():
    assert RSI2PullbackStrategy._extract_current_price(None) == 0
    assert RSI2PullbackStrategy._extract_current_price(ResCommonResponse(rt_cd="1", msg1="fail", data=None)) == 0
    assert RSI2PullbackStrategy._extract_current_price(ResCommonResponse(rt_cd="0", msg1="OK", data={})) == 0
    assert RSI2PullbackStrategy._extract_current_price(ResCommonResponse(rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "bad"}})) == 0
    assert RSI2PullbackStrategy._extract_current_price(ResCommonResponse(rt_cd="0", msg1="OK", data=SimpleNamespace(stck_prpr="12345"))) == 12345


def test_latest_ma_handles_invalid_responses():
    assert RSI2PullbackStrategy._latest_ma(RuntimeError("boom")) is None
    assert RSI2PullbackStrategy._latest_ma(None) is None
    assert RSI2PullbackStrategy._latest_ma(ResCommonResponse(rt_cd="1", msg1="fail", data=[])) is None
    assert RSI2PullbackStrategy._latest_ma(ResCommonResponse(rt_cd="0", msg1="OK", data=[{"ma": "bad"}])) is None


def test_calculate_qty_variable_sizing_and_invalid_price(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    cfg = RSI2PullbackConfig(use_fixed_qty=False, total_portfolio_krw=1_000_000, position_size_pct=10.0, min_qty=1)
    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, config=cfg, logger=logger)

    assert strategy._calculate_qty(0) == 1
    assert strategy._calculate_qty(10_000, risk_off=False) == 10
    assert strategy._calculate_qty(10_000, risk_off=True) == 5


def test_state_file_load_and_save_round_trip(tmp_path, mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    state_file = tmp_path / "rsi2_state.json"
    state_file.write_text(
        (
            '{"positions": {"005930": {"entry_price": 10000, "entry_date": "20260430", '
            '"entry_rsi": 8.5, "risk_off_entry": true}}, "cooldown": {"000660": "20260502"}}'
        ),
        encoding="utf-8",
    )

    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger, state_file=state_file)
    assert strategy._position_state["005930"].entry_price == 10000
    assert strategy._cooldown["000660"] == "20260502"

    strategy._position_state["035720"] = RSI2PositionState(
        entry_price=50000,
        entry_date="20260430",
        entry_rsi=7.0,
    )
    strategy._save_state()

    saved = state_file.read_text(encoding="utf-8")
    assert "035720" in saved


def test_state_load_and_save_errors_are_logged(tmp_path, mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    bad_state = tmp_path / "bad.json"
    bad_state.write_text("{bad json", encoding="utf-8")

    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger, state_file=bad_state)
    logger.error.assert_called()

    strategy.STATE_FILE = str(tmp_path)
    strategy._save_state()
    assert logger.error.call_count >= 2


# ── 인터페이스 ────────────────────────────────────────────────────

def test_name_property(mock_deps):
    sqs, universe, indicator, tm, logger = mock_deps
    strategy = RSI2PullbackStrategy(sqs, universe, indicator, tm, logger=logger)
    assert strategy.name == "RSI2눌림목"
