"""전략별 최근 거래 성과 저하 분석 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.strategy_performance_degradation_service import (
    StrategyPerformanceDegradationConfig,
    analyze_strategy_performance_degradation,
    compute_strategy_window_metrics,
)


FIXTURE_DIR = Path("tests/fixtures/strategy_degradation")


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_compute_strategy_window_metrics_uses_recent_closed_trades_only():
    """SOLD 거래만 청산 시각 우선으로 정렬한 뒤 최근 window를 집계한다."""
    records = _load("recent_trades_live.json")
    expected = _load("expected_metrics.json")["S1_window_5"]

    metrics = compute_strategy_window_metrics(records, window_size=5, capital_base_won=100_000)
    s1 = metrics["S1"]

    assert s1["trade_count"] == expected["trade_count"]
    assert s1["win_rate"] == pytest.approx(expected["win_rate"])
    assert s1["avg_net_return"] == pytest.approx(expected["avg_net_return"])
    assert s1["total_net_pnl"] == pytest.approx(expected["total_net_pnl"])
    assert s1["payoff_ratio"] == pytest.approx(expected["payoff_ratio"])
    assert s1["profit_factor"] == pytest.approx(expected["profit_factor"], rel=1e-4)
    assert s1["mdd_amount"] == pytest.approx(expected["mdd_amount"])
    assert s1["mdd_ratio"] == pytest.approx(0.03)
    assert s1["max_consecutive_losses"] == expected["max_consecutive_losses"]
    assert s1["avg_mfe"] == pytest.approx(expected["avg_mfe"])
    assert s1["avg_mae"] == pytest.approx(expected["avg_mae"])
    assert s1["missing_mfe_count"] == expected["missing_mfe_count"]
    assert s1["missing_mae_count"] == expected["missing_mae_count"]

    assert metrics["S2"]["trade_count"] == 1


def test_compute_window_metrics_emits_return_distribution_moments():
    """net_return 시계열에서 Sharpe / skew / (비초과)kurtosis를 산출한다 (P1 1-7 DSR 입력)."""
    records = [
        {"strategy": "S1", "status": "SOLD", "net_return": r, "net_pnl": r * 1000.0}
        for r in (1.0, 2.0, 3.0, 4.0, 5.0)
    ]

    metrics = compute_strategy_window_metrics(records, window_size=5)["S1"]

    assert metrics["sharpe_ratio"] == pytest.approx(1.897367, abs=1e-5)
    assert metrics["return_skew"] == pytest.approx(0.0, abs=1e-9)
    # non-excess kurtosis: normal == 3.0, this platykurtic ramp == 1.7.
    assert metrics["return_kurtosis"] == pytest.approx(1.7, abs=1e-9)


def test_compute_window_metrics_moments_none_for_tiny_samples():
    """표본이 너무 작으면 고차 모멘트는 None (skew>=3, kurtosis>=4)."""
    records = [
        {"strategy": "S1", "status": "SOLD", "net_return": 1.0, "net_pnl": 1000.0},
        {"strategy": "S1", "status": "SOLD", "net_return": 2.0, "net_pnl": 2000.0},
    ]

    metrics = compute_strategy_window_metrics(records, window_size=5)["S1"]

    assert metrics["sharpe_ratio"] is not None
    assert metrics["return_skew"] is None
    assert metrics["return_kurtosis"] is None


def test_analyze_marks_degraded_candidate_against_baseline():
    live = _load("recent_trades_live.json")
    backtest = _load("recent_trades_backtest.json")
    cfg = StrategyPerformanceDegradationConfig(
        window_size=5,
        min_live_trades=5,
        min_baseline_trades=5,
        capital_base_won=100_000,
        warn_win_rate_drop_pctp=30.0,
        warn_avg_return_drop_pctp=1.0,
        warn_profit_factor_below=1.0,
        critical_consecutive_losses=3,
    )

    result = analyze_strategy_performance_degradation(live, backtest, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "critical_candidate"
    assert "win_rate_drop" in s1["reasons"]
    assert "avg_return_drop" in s1["reasons"]
    assert "profit_factor_low" in s1["reasons"]
    assert "consecutive_losses" in s1["reasons"]
    assert "pause_new_entries_candidate" in s1["recommended_actions"]
    assert "paper_mode_candidate" in s1["recommended_actions"]
    assert result["candidates"][0]["strategy"] == "S1"


def test_analyze_separates_insufficient_live_and_baseline_samples():
    live = _load("recent_trades_live.json")
    backtest = _load("recent_trades_backtest.json")[:1]
    cfg = StrategyPerformanceDegradationConfig(
        window_size=5,
        min_live_trades=5,
        min_baseline_trades=5,
    )

    result = analyze_strategy_performance_degradation(live, backtest, cfg)

    assert result["strategies"]["S2"]["status"] == "insufficient_live"
    assert result["strategies"]["S1"]["status"] == "insufficient_baseline"
    assert result["candidates"] == []
