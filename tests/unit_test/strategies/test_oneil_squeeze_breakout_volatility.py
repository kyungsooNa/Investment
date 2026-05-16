# tests/unit_test/strategies/test_oneil_squeeze_breakout_volatility.py
"""TradeSignal.volatility_20d_annualized 전파 검증.

BUY (scan): OSBWatchlistItem.volatility_20d_annualized -> TradeSignal.volatility_20d_annualized
SELL (시간손절/추세이탈): 신선하게 fetched ohlcv 의 closes 로 annualized_return_std 계산 -> TradeSignal
SELL (손절/트레일링/본절): ohlcv 미로딩 경로 -> volatility_20d_annualized=None
"""
from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from core.market_clock import MarketClock
from services.oneil_universe_service import OneilUniverseService
from services.stock_query_service import StockQueryService
from strategies.oneil_common_types import (
    OneilBreakoutConfig,
    OSBPositionState,
    OSBWatchlistItem,
)
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from utils.volatility_utils import annualized_return_std


@pytest.fixture
def mock_deps():
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


@pytest.fixture(autouse=True)
def _block_async_file_io(monkeypatch):
    """check_exits -> _save_state_async/_load_state_async 비활성화."""
    monkeypatch.setattr(OneilSqueezeBreakoutStrategy, "_save_state_async", AsyncMock())
    monkeypatch.setattr(OneilSqueezeBreakoutStrategy, "_load_state_async", AsyncMock())


def _build_strategy(mock_deps):
    sqs, universe, tm, logger = mock_deps
    strategy = OneilSqueezeBreakoutStrategy(sqs, universe, tm, logger=logger)
    strategy._position_state = {}
    strategy._save_state = MagicMock()
    return strategy, sqs, universe, tm, logger


def _candidate_item(volatility: float | None) -> OSBWatchlistItem:
    return OSBWatchlistItem(
        code="005930",
        name="Samsung",
        market="KOSPI",
        high_20d=70000,
        ma_20d=68000,
        ma_50d=65000,
        avg_vol_20d=100000,
        bb_width_min_20d=1000,
        prev_bb_width=1100,
        w52_hgpr=80000,
        avg_trading_value_5d=50_000_000_000,
        market_cap=100_000_000_000,
        volatility_20d_annualized=volatility,
    )


@pytest.mark.asyncio
async def test_buy_signal_carries_item_volatility(mock_deps):
    """BUY 신호는 OSBWatchlistItem.volatility_20d_annualized 를 그대로 전파한다."""
    strategy, sqs, universe, tm, _ = _build_strategy(mock_deps)

    expected_volatility = 0.3217
    item = _candidate_item(volatility=expected_volatility)

    universe.get_watchlist.return_value = {"005930": item}
    universe.is_market_timing_ok.return_value = True
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    # 정상 돌파 케이스 (기존 test_scan_buy_signal 와 동일한 수치)
    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={
            "output": {
                "stck_prpr": "71000",
                "acml_vol": "200000",
                "pgtr_ntby_qty": "30000",
                "acml_tr_pbmn": "14200000000",
                "stck_hgpr": "71500",
                "stck_lwpr": "65000",
            }
        },
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].action == "BUY"
    assert signals[0].volatility_20d_annualized == pytest.approx(expected_volatility)


@pytest.mark.asyncio
async def test_buy_signal_propagates_none_volatility(mock_deps):
    """item.volatility_20d_annualized 가 None 이면 신호도 None 그대로 둔다."""
    strategy, sqs, universe, tm, _ = _build_strategy(mock_deps)

    universe.get_watchlist.return_value = {"005930": _candidate_item(volatility=None)}
    universe.is_market_timing_ok.return_value = True
    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)
    tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={
            "output": {
                "stck_prpr": "71000",
                "acml_vol": "200000",
                "pgtr_ntby_qty": "30000",
                "acml_tr_pbmn": "14200000000",
                "stck_hgpr": "71500",
                "stck_lwpr": "65000",
            }
        },
    )
    sqs.get_stock_conclusion.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": [{"tday_rltv": "150.0"}]}
    )

    signals = await strategy.scan()

    assert len(signals) == 1
    assert signals[0].volatility_20d_annualized is None


@pytest.mark.asyncio
async def test_sell_signal_stop_loss_no_ohlcv_path_volatility_none(mock_deps):
    """손절 경로는 ohlcv 미로딩 → SELL 신호 volatility=None."""
    strategy, sqs, _, tm, _ = _build_strategy(mock_deps)

    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 진입가 대비 -10% 하락하여 손절 트리거 (기본 stop_loss_pct = -7%)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10_000,
        entry_date="20241201",
        peak_price=10_000,
        breakout_level=10_000,
    )

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": {"stck_prpr": "9000", "acml_vol": "100000"}},
    )

    holdings = [{"code": "005930", "name": "Samsung", "qty": 10, "buy_price": 10_000}]
    signals = await strategy.check_exits(holdings)

    sell = [s for s in signals if s.action == "SELL"]
    assert len(sell) == 1
    assert "손절" in sell[0].reason
    assert sell[0].volatility_20d_annualized is None
    sqs.get_recent_daily_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_sell_signal_trend_break_uses_ohlcv_volatility(mock_deps, monkeypatch):
    """OHLCV 로딩 경로 (시간손절/추세이탈)에서 ohlcv 의 closes 로 변동성 계산 후 SELL 신호에 부착."""
    strategy, sqs, _, tm, _ = _build_strategy(mock_deps)

    tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 12, 0, 0)

    # 손절/트레일링/본절 모두 비트리거: 진입가=현재가=peak (pnl 0%)
    strategy._position_state["005930"] = OSBPositionState(
        entry_price=10_000,
        entry_date="20241201",
        peak_price=10_000,
        breakout_level=10_000,
    )

    closes = [10_000 + (i % 5) * 50 for i in range(60)]
    ohlcv = [
        {
            "date": f"2024{(i % 12) + 1:02d}{(i % 28) + 1:02d}",
            "open": c,
            "high": c + 100,
            "low": c - 100,
            "close": c,
            "volume": 1_000_000,
        }
        for i, c in enumerate(closes)
    ]

    sqs.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="OK",
        data={"output": {"stck_prpr": "10000", "acml_vol": "1000000"}},
    )
    sqs.get_recent_daily_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=ohlcv
    )

    # _check_trend_break 를 강제로 True 반환 — ohlcv 로딩 경로에 도달 시 SELL 발동
    monkeypatch.setattr(
        OneilSqueezeBreakoutStrategy,
        "_check_trend_break",
        lambda self, code, current_price, current_vol, ohlcv_arg: (True, "추세이탈(테스트)"),
    )

    holdings = [{"code": "005930", "name": "Samsung", "qty": 10, "buy_price": 10_000}]
    signals = await strategy.check_exits(holdings)

    sell = [s for s in signals if s.action == "SELL"]
    assert len(sell) == 1
    # 시간손절 또는 추세이탈 — 어느 쪽이든 ohlcv 로딩 경로 (volatility 부착 대상)
    assert any(keyword in sell[0].reason for keyword in ("추세이탈", "시간손절"))
    expected = annualized_return_std([r["close"] for r in ohlcv])
    assert expected is not None
    assert sell[0].volatility_20d_annualized == pytest.approx(expected)
    sqs.get_recent_daily_ohlcv.assert_called_once()


def test_universe_item_field_default_is_none():
    """OSBWatchlistItem.volatility_20d_annualized 기본값은 None (역호환)."""
    item = OSBWatchlistItem(
        code="005930",
        name="Samsung",
        market="KOSPI",
        high_20d=70000,
        ma_20d=68000,
        ma_50d=65000,
        avg_vol_20d=100000,
        bb_width_min_20d=1000,
        prev_bb_width=1100,
        w52_hgpr=80000,
        avg_trading_value_5d=50_000_000_000,
    )
    assert item.volatility_20d_annualized is None


def test_annualized_return_std_matches_strategy_expectation():
    """OSB 가 사용하는 헬퍼는 closes 만으로 결정적인 값을 반환."""
    closes = [100.0 + i for i in range(30)]  # 단조 증가 (변동성 ↑)
    result = annualized_return_std(closes)
    assert result is not None
    assert result > 0
    assert math.isfinite(result)
