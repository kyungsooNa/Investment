from __future__ import annotations

from services.multiple_testing_bias_service import compute_multiple_testing_bias_summary


def test_multiple_testing_bias_summary_skips_when_trial_count_is_small():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 100},
            "S2": {"total_net_pnl": 50},
        },
        min_trials=3,
    )

    assert summary["trial_count"] == 2
    assert summary["bias_warning"] is False
    assert summary["warning_reasons"] == []


def test_multiple_testing_bias_summary_warns_when_best_towers_over_median():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 1_000},
            "S2": {"total_net_pnl": 100},
            "S3": {"total_net_pnl": 80},
            "S4": {"total_net_pnl": 60},
            "S5": {"total_net_pnl": 40},
        },
        min_trials=5,
        top_to_median_warning_ratio=3.0,
    )

    assert summary["bias_warning"] is True
    assert summary["best_strategy"] == "S1"
    assert summary["median_value"] == 80.0
    assert summary["top_to_median_ratio"] == 12.5
    assert "best_over_median_ratio_high" in summary["warning_reasons"]


def test_multiple_testing_bias_summary_warns_when_best_positive_but_median_non_positive():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 100},
            "S2": {"total_net_pnl": 0},
            "S3": {"total_net_pnl": -10},
            "S4": {"total_net_pnl": -20},
            "S5": {"total_net_pnl": -30},
        },
        min_trials=5,
    )

    assert summary["bias_warning"] is True
    assert summary["median_value"] == -10.0
    assert summary["top_to_median_ratio"] is None
    assert "best_positive_median_non_positive" in summary["warning_reasons"]


def test_multiple_testing_bias_summary_reports_deflated_sharpe_proxy():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 1000, "sharpe_ratio": 2.2},
            "S2": {"total_net_pnl": 800, "sharpe_ratio": 1.8},
            "S3": {"total_net_pnl": 600, "sharpe_ratio": 1.2},
            "S4": {"total_net_pnl": 400, "sharpe_ratio": 0.9},
            "S5": {"total_net_pnl": 200, "sharpe_ratio": 0.7},
        },
        min_trials=5,
        min_adjusted_sharpe=1.5,
    )

    dsr = summary["deflated_sharpe_proxy"]
    assert dsr["available"] is True
    assert dsr["best_strategy"] == "S1"
    assert dsr["best_sharpe"] == 2.2
    assert dsr["adjusted_sharpe"] < dsr["best_sharpe"]
    assert dsr["passed"] is False
    assert "deflated_sharpe_below_threshold" in summary["warning_reasons"]


def test_multiple_testing_bias_summary_reports_pbo_proxy():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 1000, "in_sample_net_pnl": 1000, "out_of_sample_net_pnl": -20},
            "S2": {"total_net_pnl": 900, "in_sample_net_pnl": 900, "out_of_sample_net_pnl": -10},
            "S3": {"total_net_pnl": 200, "in_sample_net_pnl": 200, "out_of_sample_net_pnl": 80},
            "S4": {"total_net_pnl": 100, "in_sample_net_pnl": 100, "out_of_sample_net_pnl": 60},
            "S5": {"total_net_pnl": 50, "in_sample_net_pnl": 50, "out_of_sample_net_pnl": 40},
        },
        min_trials=5,
        max_pbo_probability=0.5,
    )

    pbo = summary["pbo_proxy"]
    assert pbo["available"] is True
    assert pbo["top_in_sample_strategies"] == ["S1", "S2"]
    assert pbo["pbo_probability"] == 1.0
    assert pbo["passed"] is False
    assert "pbo_probability_above_threshold" in summary["warning_reasons"]
