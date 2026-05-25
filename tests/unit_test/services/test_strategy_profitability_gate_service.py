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


def test_profitability_gate_warns_on_regime_balance_gaps_without_blocking():
    records = [
        _sold(
            120,
            2.0,
            signal_time="2026-05-01",
            regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"},
        ),
        _sold(
            -20,
            -0.2,
            signal_time="2026-05-02",
            regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"},
        ),
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
        regime_balance_required_buckets=("KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR"),
        regime_balance_min_trades=2,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "pass"
    assert "regime_balance_incomplete" in s1["warnings"]
    assert s1["regime_balance"]["balanced_pass"] is False
    assert s1["regime_balance"]["missing_regimes"] == [
        "KOSDAQ_BULL",
        "SIDEWAYS",
        "BEAR",
    ]


def test_profitability_gate_reports_multiple_testing_bias_warning():
    records = []
    for strategy, pnl in [
        ("S1", 1_000),
        ("S2", 100),
        ("S3", 80),
        ("S4", 60),
        ("S5", 40),
    ]:
        records.append(_sold(pnl, 1.0, strategy=strategy, signal_time="2026-05-01"))

    cfg = StrategyProfitabilityGateConfig(
        min_trades=1,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=0.0,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        multiple_testing_min_trials=5,
        multiple_testing_top_to_median_warning_ratio=3.0,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "multiple_testing_bias_warning" in result["warnings"]
    assert result["multiple_testing_bias"]["bias_warning"] is True
    assert result["multiple_testing_bias"]["best_strategy"] == "S1"


def test_profitability_gate_reports_strategy_correlation_warning():
    records = []
    for day, s1_ret, s2_ret in [
        ("2026-05-01", 1.0, 2.0),
        ("2026-05-02", 2.0, 4.0),
        ("2026-05-03", 3.0, 6.0),
    ]:
        records.append(_sold(100, s1_ret, strategy="S1", signal_time=day))
        records.append(_sold(100, s2_ret, strategy="S2", signal_time=day))

    cfg = StrategyProfitabilityGateConfig(
        min_trades=3,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=0.0,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        strategy_correlation_min_overlap=3,
        strategy_correlation_warning_threshold=0.9,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "strategy_correlation_high" in result["warnings"]
    assert result["strategy_correlation"]["max_positive_pair"]["left"] == "S1"
    assert result["strategy_correlation"]["max_positive_pair"]["right"] == "S2"


def test_profitability_gate_reports_market_beta_warning():
    records = [
        {
            "status": "SOLD",
            "strategy": "S1",
            "signal_time": "2026-05-01",
            "net_pnl": 100,
            "net_return": 2.0,
            "market_return": 1.0,
        },
        {
            "status": "SOLD",
            "strategy": "S1",
            "signal_time": "2026-05-02",
            "net_pnl": 200,
            "net_return": 4.0,
            "market_return": 2.0,
        },
        {
            "status": "SOLD",
            "strategy": "S1",
            "signal_time": "2026-05-03",
            "net_pnl": 300,
            "net_return": 6.0,
            "market_return": 3.0,
        },
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=3,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=None,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        market_beta_min_overlap=3,
        market_beta_warning_threshold=1.5,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "portfolio_market_beta_high" in result["warnings"]
    assert "strategy_market_beta_high" in result["warnings"]
    assert result["market_beta"]["portfolio"]["beta"] == pytest.approx(2.0)
    assert result["market_beta"]["high_beta_strategies"][0]["strategy"] == "S1"


def test_profitability_gate_reports_daily_entry_pressure_warning():
    records = [
        {"status": "FILLED", "side": "BUY", "strategy": "S1", "signal_time": "2026-05-01 09:00:00"},
        {"status": "FILLED", "side": "BUY", "strategy": "S2", "signal_time": "2026-05-01 09:01:00"},
        {"status": "FILLED", "side": "BUY", "strategy": "S3", "signal_time": "2026-05-01 09:02:00"},
        _sold(100, 1.0, strategy="S1", signal_time="2026-05-02"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=1,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=0.0,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        daily_entry_warning_threshold=3,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "portfolio_daily_entry_pressure_high" in result["warnings"]
    assert result["entry_pressure"]["max_daily_entry_date"] == "2026-05-01"
    assert result["entry_pressure"]["max_daily_entry_count"] == 3


def test_profitability_gate_reports_intraday_entry_pressure_warning():
    records = [
        {"status": "FILLED", "side": "BUY", "strategy": "S1", "signal_time": "2026-05-01 14:30:00"},
        {"status": "FILLED", "side": "BUY", "strategy": "S2", "signal_time": "2026-05-01 15:00:00"},
        _sold(100, 1.0, strategy="S1", signal_time="2026-05-02"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=1,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=0.0,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        daily_entry_warning_threshold=5,
        opening_entry_warning_threshold=3,
        closing_entry_warning_threshold=2,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "portfolio_closing_entry_pressure_high" in result["warnings"]
    assert result["entry_pressure"]["intraday_windows"]["closing"]["max_entry_count"] == 2
    assert result["entry_pressure"]["intraday_windows"]["closing"]["max_entry_date"] == "2026-05-01"


def test_profitability_gate_reports_consecutive_loss_cooldown_candidate():
    records = [
        _sold(-100, -1.0, strategy="S1", signal_time="2026-05-01"),
        _sold(-50, -0.5, strategy="S1", signal_time="2026-05-02"),
        _sold(-30, -0.3, strategy="S1", signal_time="2026-05-03"),
    ]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=3,
        min_profit_factor=None,
        min_payoff_ratio=None,
        min_win_rate=0.0,
        min_avg_net_return=None,
        require_positive_total_net_pnl=False,
        max_mdd_pct=None,
        max_monte_carlo_ruin_probability=None,
        max_monte_carlo_worst_mdd_pct=None,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
        consecutive_loss_warning_threshold=3,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    assert "portfolio_consecutive_loss_cooldown_candidate" in result["warnings"]
    assert result["cooldown"]["candidates"][0]["strategy"] == "S1"
    assert result["cooldown"]["candidates"][0]["max_consecutive_losses"] == 3


def test_profitability_gate_warns_when_monte_carlo_unavailable_by_default():
    records = [_sold(100, 1.0, signal_time="2026-05-01"), _sold(80, 0.5, signal_time="2026-05-02")]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.0,
        max_mdd_pct=None,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=0.05,
        max_monte_carlo_worst_mdd_pct=30.0,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "pass"
    assert "monte_carlo_unavailable" in s1["warnings"]
    assert "monte_carlo_unavailable" not in s1["blocking_reasons"]


def test_profitability_gate_blocks_when_monte_carlo_required_and_missing():
    records = [_sold(100, 1.0, signal_time="2026-05-01"), _sold(80, 0.5, signal_time="2026-05-02")]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.0,
        max_mdd_pct=None,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=0.05,
        max_monte_carlo_worst_mdd_pct=30.0,
        require_monte_carlo=True,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "fail"
    assert s1["passed"] is False
    assert "monte_carlo_unavailable" in s1["blocking_reasons"]
    assert "monte_carlo_unavailable" not in s1["warnings"]


def test_profitability_gate_require_monte_carlo_passes_when_evidence_provided():
    records = [_sold(100, 1.0, signal_time="2026-05-01"), _sold(80, 0.5, signal_time="2026-05-02")]
    cfg = StrategyProfitabilityGateConfig(
        min_trades=2,
        min_profit_factor=1.0,
        min_payoff_ratio=1.0,
        min_win_rate=0.5,
        min_avg_net_return=0.0,
        max_mdd_pct=None,
        capital_base_won=1_000,
        max_monte_carlo_ruin_probability=0.05,
        max_monte_carlo_worst_mdd_pct=30.0,
        require_monte_carlo=True,
        require_non_negative_regime_pnl=False,
        regime_balance_required_buckets=(),
    )

    result = evaluate_strategy_profitability_gate(
        records,
        cfg,
        monte_carlo={"ruin_probability": 0.01, "worst_max_drawdown_pct": 5.0},
    )

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "pass"
    assert "monte_carlo_unavailable" not in s1["blocking_reasons"]
    assert "monte_carlo_unavailable" not in s1["warnings"]


def test_profitability_gate_blocks_when_regime_balance_required_and_incomplete():
    records = [
        _sold(
            120,
            2.0,
            signal_time="2026-05-01",
            regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"},
        ),
        _sold(
            -20,
            -0.2,
            signal_time="2026-05-02",
            regime={"kospi": "bull", "kosdaq": "bull", "stock_market": "KOSPI"},
        ),
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
        regime_balance_required_buckets=("KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR"),
        regime_balance_min_trades=2,
        require_regime_balance=True,
    )

    result = evaluate_strategy_profitability_gate(records, cfg)

    s1 = result["strategies"]["S1"]
    assert s1["status"] == "fail"
    assert s1["passed"] is False
    assert "regime_balance_incomplete" in s1["blocking_reasons"]
    assert "regime_balance_incomplete" not in s1["warnings"]
