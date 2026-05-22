from __future__ import annotations

import pytest

from services.strategy_profitability_gate_service import (
    StrategyProfitabilityGateConfig,
    evaluate_strategy_profitability_gate,
)


def _sold(
    pnl: float,
    ret: float,
    *,
    strategy: str = "S1",
    signal_time: str = "2026-05-01",
    regime: dict | None = None,
) -> dict:
    record = {
        "status": "SOLD",
        "strategy": strategy,
        "net_pnl": pnl,
        "net_return": ret,
        "signal_time": signal_time,
    }
    if regime is not None:
        record["market_regime"] = regime
    return record


def test_profitability_gate_passes_when_core_thresholds_are_met():
    records = [
        _sold(120, 2.0, signal_time="2026-05-01"),
        _sold(-40, -0.5, signal_time="2026-05-02"),
        _sold(100, 1.5, signal_time="2026-05-03"),
        _sold(-30, -0.4, signal_time="2026-05-04"),
        _sold(80, 1.0, signal_time="2026-05-05"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=5,
        min_profit_factor=1.5,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.2,
        max_mdd_pct=10.0,
        capital_base_won=1_000,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "pass"
    assert s1["passed"] is True
    assert s1["blocking_reasons"] == []
    assert s1["metrics"]["profit_factor"] == pytest.approx(300 / 70)
    assert result["summary"]["pass_count"] == 1


def test_profitability_gate_marks_insufficient_sample_before_metric_failures():
    records = [_sold(-100, -2.0), _sold(-50, -1.0)]
    cfg = StrategyProfitabilityGateConfig(min_trades=3, capital_base_won=1_000)

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "insufficient_sample"
    assert s1["passed"] is False
    assert s1["blocking_reasons"] == ["insufficient_trades"]


def test_profitability_gate_fails_on_profitability_drawdown_monte_carlo_and_regime():
    records = [
        _sold(100, 1.0, signal_time="2026-05-01", regime={"kospi": "bear", "kosdaq": "sideways"}),
        _sold(-130, -2.0, signal_time="2026-05-02", regime={"kospi": "bear", "kosdaq": "sideways"}),
        _sold(80, 0.8, signal_time="2026-05-03", regime={"kospi": "bear", "kosdaq": "sideways"}),
        _sold(-140, -2.1, signal_time="2026-05-04", regime={"kospi": "bear", "kosdaq": "sideways"}),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=4,
        min_profit_factor=1.2,
        min_payoff_ratio=1.0,
        min_win_rate=0.6,
        min_avg_net_return=0.0,
        max_mdd_pct=10.0,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=0.05,
        max_monte_carlo_worst_mdd_pct=15.0,
        min_regime_trade_count=2,
        require_non_negative_regime_pnl=True,
    )

    result = evaluate_strategy_profitability_gate(
        records,
        cfg,
        monte_carlo={
            "ruin_probability": 0.25,
            "worst_max_drawdown_pct": 22.0,
        },
    )

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "fail"
    assert s1["passed"] is False
    assert "profit_factor_below" in s1["blocking_reasons"]
    assert "win_rate_below" in s1["blocking_reasons"]
    assert "avg_net_return_below" in s1["blocking_reasons"]
    assert "mdd_pct_above" in s1["blocking_reasons"]
    assert "monte_carlo_ruin_probability_above" in s1["blocking_reasons"]
    assert "monte_carlo_worst_mdd_pct_above" in s1["blocking_reasons"]
    assert "regime_BEAR_negative_pnl" in s1["blocking_reasons"]
    assert result["summary"]["fail_count"] == 1


def test_profitability_gate_blocks_on_parameter_stability_cliff():
    records = [
        _sold(120, 2.0, signal_time="2026-05-01"),
        _sold(-20, -0.2, signal_time="2026-05-02"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.0,
        max_mdd_pct=10.0,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
    )
    parameter_stability = {
        "summary": {
            "dimensions": {
                "pp_ma_proximity_upper_pct": {
                    "stability": {
                        "flag": "cliff",
                        "reason": "neighbor_sign_flip_or_steep_drop",
                    }
                }
            }
        }
    }

    result = evaluate_strategy_profitability_gate(
        records,
        cfg,
        parameter_stability=parameter_stability,
    )

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "fail"
    assert "parameter_stability_cliff:pp_ma_proximity_upper_pct" in s1["blocking_reasons"]
    assert s1["parameter_stability"]["blocked_flags"] == ["cliff", "spike"]
    assert s1["parameter_stability"]["issues"] == [
        {
            "dimension": "pp_ma_proximity_upper_pct",
            "flag": "cliff",
            "reason": "neighbor_sign_flip_or_steep_drop",
        }
    ]


def test_profitability_gate_can_leave_parameter_stability_as_report_only():
    records = [
        _sold(120, 2.0, signal_time="2026-05-01"),
        _sold(-20, -0.2, signal_time="2026-05-02"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.0,
        max_mdd_pct=10.0,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        block_parameter_stability_flags=(),
    )

    result = evaluate_strategy_profitability_gate(
        records,
        cfg,
        parameter_stability={
            "summary": {
                "dimensions": {
                    "pp_ma_proximity_upper_pct": {"stability": {"flag": "cliff"}}
                }
            }
        },
    )

    assert result["strategies"]["S1"]["status"] == "pass"
