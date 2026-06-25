# tests/unit_test/strategies/test_inverse_etf_regime_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from common.types import ResCommonResponse, TradeSignal
from strategies.inverse_etf_regime_strategy import InverseEtfRegimeStrategy
from strategies.inverse_etf_regime_types import (
    InverseEtfRegimeConfig,
    InverseEtfPositionState,
)
from services.stock_query_service import StockQueryService
from services.indicator_service import IndicatorService
from services.market_regime_service import MarketRegimeService, RegimeSnapshot
from core.market_clock import MarketClock


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _price_resp(current="10000"):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_prpr": current}},
    )


def _ma_resp(value):
    return ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"code": "114800", "date": "20250101", "close": 10000.0, "ma": value}],
    )


def _regime(label):
    return RegimeSnapshot(
        market="KOSPI",
        trend_status="hard_decline" if label == "bear" else "rising",
        regime_label=label,
        snapshot_date="20250102",
        is_rising=(label != "bear"),
        net_change_pct=-0.5 if label == "bear" else 0.5,
        max_daily_drop_pct=-0.3 if label == "bear" else 0.0,
    )


# ── 공통 Fixture ──────────────────────────────────────────────────

@pytest.fixture
def mock_deps():
    sqs = MagicMock(spec=StockQueryService)
    regime = MagicMock(spec=MarketRegimeService)
    indicator = MagicMock(spec=IndicatorService)
    tm = MagicMock(spec=MarketClock)
    logger = MagicMock()

    sqs.get_current_price = AsyncMock(spec=StockQueryService.get_current_price)
    regime.classify = AsyncMock(spec=MarketRegimeService.classify)
    indicator.get_moving_average = AsyncMock(spec=IndicatorService.get_moving_average)

    return sqs, regime, indicator, tm, logger


@pytest.fixture
def strategy(mock_deps):
    sqs, regime, indicator, tm, logger = mock_deps
    strat = InverseEtfRegimeStrategy(sqs, regime, indicator, tm, logger=logger)
    strat._position_state = {}
    strat._cooldown = {}
    strat._save_state = MagicMock()
    strat._load_state = MagicMock()
    tm.get_current_kst_time.return_value = datetime(2025, 1, 2, 15, 15, 0)
    return strat


@pytest.fixture
def bear_setup(strategy, mock_deps):
    """모든 진입 조건(베어 레짐 + 추세 확인 + 유효 현재가)이 통과하는 셋업."""
    sqs, regime, indicator, tm, logger = mock_deps
    regime.classify.return_value = _regime("bear")
    indicator.get_moving_average.return_value = _ma_resp(9500.0)  # current(10000) > MA
    sqs.get_current_price.return_value = _price_resp("10000")
    return strategy, sqs, regime, indicator, tm, logger


# ── scan() 진입 테스트 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_emits_buy_when_bear_and_trend_confirmed(bear_setup):
    """베어 레짐 + 인버스 ETF 현재가 > MA → BUY 시그널 1건."""
    strat, _, _, _, _, _ = bear_setup
    signals = await strat.scan()
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, TradeSignal)
    assert sig.action == "BUY"
    assert sig.code == "114800"
    assert sig.strategy_name == strat.name
    assert sig.entry_reason == "inverse_etf_bear_regime"


@pytest.mark.asyncio
async def test_scan_no_signal_when_not_bear(bear_setup):
    """레짐이 베어가 아니면(상승/횡보) 진입하지 않는다 — R-2 디코릴레이션 핵심."""
    strat, _, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bull")
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_sideways(bear_setup):
    """횡보장에서도 진입하지 않는다(휩쏘 방지)."""
    strat, _, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("sideways")
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_below_ma(bear_setup):
    """베어라도 인버스 ETF가 추세 미확인(현재가 <= MA)이면 진입하지 않는다."""
    strat, sqs, _, indicator, _, _ = bear_setup
    indicator.get_moving_average.return_value = _ma_resp(10500.0)  # current(10000) < MA
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_signal_when_ma_unavailable(bear_setup):
    """MA 조회 실패 시 진입하지 않는다(보수)."""
    strat, _, _, indicator, _, _ = bear_setup
    indicator.get_moving_average.return_value = ResCommonResponse(rt_cd="1", msg1="err", data=None)
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_skips_when_already_holding(bear_setup):
    """이미 포지션 보유 중이면 중복 진입하지 않는다."""
    strat, _, _, _, _, _ = bear_setup
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=9000, entry_date="20250101", peak_price=9000)
    }
    signals = await strat.scan()
    assert signals == []


@pytest.mark.asyncio
async def test_scan_buy_signal_has_stop_and_trailing_fields(bear_setup):
    """BUY 시그널은 손절선·트레일링 규칙 등 P3-4 9필드를 채운다."""
    strat, _, _, _, _, _ = bear_setup
    sig = (await strat.scan())[0]
    assert sig.stop_loss_price is not None
    assert sig.stop_loss_price < sig.price
    assert sig.trailing_rule is not None
    assert sig.required_data


# ── check_exits() 청산 테스트 ──────────────────────────────────────

def _hold(code="114800", buy_price=10000, qty=3):
    return {"code": code, "name": "KODEX 인버스", "buy_price": buy_price, "qty": qty}


@pytest.mark.asyncio
async def test_exit_when_regime_flips_to_bull(bear_setup):
    """레짐이 베어에서 이탈하면 즉시 청산(헤지 목적 종료)."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bull")
    sqs.get_current_price.return_value = _price_resp("10000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10000)
    }
    signals = await strat.check_exits([_hold()])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "레짐" in signals[0].reason


@pytest.mark.asyncio
async def test_exit_on_hard_stop(bear_setup):
    """베어 유지 중이라도 진입가 대비 하드 스탑 도달 시 손절."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("9000")  # -10% < -5%
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10000)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "스탑" in signals[0].reason or "손절" in signals[0].reason


@pytest.mark.asyncio
async def test_exit_on_trailing_stop_from_peak(bear_setup):
    """고점 대비 트레일링 스톱(-8%) 도달 시 청산."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    # 고점 12000 기록 후 현재 11000 (고점 대비 -8.3% < -8%)
    sqs.get_current_price.return_value = _price_resp("11000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=12000)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert "트레일" in signals[0].reason


@pytest.mark.asyncio
async def test_no_exit_when_bear_and_above_stops(bear_setup):
    """베어 유지 + 스탑/트레일링 미도달이면 보유 지속."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("10500")  # +5%, 고점 갱신
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=10500)
    }
    signals = await strat.check_exits([_hold(buy_price=10000)])
    assert signals == []


@pytest.mark.asyncio
async def test_check_exits_updates_peak(bear_setup):
    """현재가가 기존 고점을 갱신하면 position_state.peak_price 가 올라간다."""
    strat, sqs, regime, _, _, _ = bear_setup
    regime.classify.return_value = _regime("bear")
    sqs.get_current_price.return_value = _price_resp("13000")
    strat._position_state = {
        "114800": InverseEtfPositionState(entry_price=10000, entry_date="20250101", peak_price=11000)
    }
    await strat.check_exits([_hold(buy_price=10000)])
    assert strat._position_state["114800"].peak_price == 13000


@pytest.mark.asyncio
async def test_check_exits_empty_holdings(strategy):
    """보유가 없으면 빈 리스트."""
    signals = await strategy.check_exits([])
    assert signals == []


# ── 식별자 ────────────────────────────────────────────────────────

def test_strategy_identifiers(strategy):
    assert strategy.strategy_id == "inverse_etf_regime"
    assert isinstance(strategy.name, str) and strategy.name
