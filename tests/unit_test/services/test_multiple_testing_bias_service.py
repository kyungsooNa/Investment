from __future__ import annotations

import random

import pytest

from services.multiple_testing_bias_service import (
    build_config_period_pnl_matrix,
    compute_multiple_testing_bias_summary,
    compute_pbo_cscv,
)


def _dominant_matrix(t_periods=80, n_configs=4):
    """config0가 모든 기간에서 일관되게 우월 → 진짜 엣지(과최적화 아님)."""
    rows = []
    for i in range(t_periods):
        row = [1.0 + (0.2 if i % 2 else 0.0)]  # config0: 평균 큰 양수, 높은 Sharpe
        for j in range(1, n_configs):
            row.append((-0.1 if i % 2 else 0.1) * j)  # 나머지: 0 근처 잡음
        rows.append(row)
    return rows


def _noise_matrix(t_periods=160, n_configs=8, seed=7):
    """진짜 엣지 없는 순수 잡음 → IS 최고는 운 → OOS 순위 무작위 → PBO ~ 0.5."""
    rng = random.Random(seed)
    return [[rng.gauss(0.0, 1.0) for _ in range(n_configs)] for _ in range(t_periods)]


def test_pbo_cscv_dominant_config_low_pbo():
    res = compute_pbo_cscv(_dominant_matrix(), n_splits=8, threshold=0.5)
    assert res["available"] is True
    assert res["pbo"] == 0.0  # 일관 우월 config는 OOS도 최상위 → 과최적화 0
    assert res["passed"] is True


def test_pbo_cscv_pure_noise_near_half():
    res = compute_pbo_cscv(_noise_matrix(), n_splits=8)
    assert res["available"] is True
    assert 0.3 <= res["pbo"] <= 0.7  # 엣지 없음 → 과최적화 확률 ~0.5


def test_pbo_cscv_unavailable_too_few_configs():
    res = compute_pbo_cscv([[1.0], [2.0], [3.0]], n_splits=4)
    assert res["available"] is False
    assert res["reason"] == "insufficient_configs"


def test_pbo_cscv_unavailable_too_few_periods():
    res = compute_pbo_cscv([[1.0, 2.0], [3.0, 4.0]], n_splits=8)
    assert res["available"] is False
    assert res["reason"] == "insufficient_periods"


def test_pbo_cscv_rejects_odd_n_splits():
    res = compute_pbo_cscv(_noise_matrix(), n_splits=7)
    assert res["available"] is False
    assert res["reason"] == "invalid_n_splits"


def test_pbo_cscv_threshold_breach_sets_passed_false():
    res = compute_pbo_cscv(_noise_matrix(), n_splits=8, threshold=0.1)
    assert res["available"] is True
    assert res["pbo"] > 0.1
    assert res["passed"] is False


def test_summary_includes_pbo_cscv_when_matrix_provided():
    summary = compute_multiple_testing_bias_summary(
        {"S1": {"total_net_pnl": 100}, "S2": {"total_net_pnl": 50}},
        returns_matrix=_noise_matrix(),
        max_pbo_cscv_probability=0.1,
    )
    assert summary["pbo_cscv"]["available"] is True
    assert summary["pbo_cscv"]["passed"] is False
    assert "pbo_cscv_above_threshold" in summary["warning_reasons"]


def test_build_matrix_buckets_net_pnl_by_date_per_config():
    records_by_config = {
        "baseline": [
            {"status": "SOLD", "signal_time": "2025-01-02 15:30:00", "net_pnl": 100.0},
            {"status": "SOLD", "signal_time": "2025-01-03 09:10:00", "net_pnl": -50.0},
            {"status": "SOLD", "signal_time": "2025-01-03 14:00:00", "net_pnl": 20.0},  # same day → 합산
        ],
        "variant_a": [
            {"status": "SOLD", "signal_time": "20250102", "net_pnl": -10.0},
            {"status": "SOLD", "signal_time": "2025-01-04", "net_pnl": 70.0},
        ],
    }
    matrix, configs, periods = build_config_period_pnl_matrix(records_by_config)

    assert configs == ["baseline", "variant_a"]
    assert periods == ["20250102", "20250103", "20250104"]
    # 행=기간, 열=config
    assert matrix == [
        [100.0, -10.0],   # 01-02
        [-30.0, 0.0],     # 01-03 (baseline -50+20, variant 없음→0)
        [0.0, 70.0],      # 01-04
    ]


def test_build_matrix_excludes_non_sold_and_empty_date():
    records_by_config = {
        "c1": [
            {"status": "SUBMITTED", "signal_time": "2025-01-02", "net_pnl": 999.0},  # 미완료 제외
            {"status": "SOLD", "signal_time": "", "net_pnl": 5.0},  # 날짜 없음 제외
            {"status": "SOLD", "signal_time": "2025-01-02", "net_pnl": 10.0},
        ],
        "c2": [{"status": "SOLD", "signal_time": "2025-01-02", "net_pnl": 3.0}],
    }
    matrix, configs, periods = build_config_period_pnl_matrix(records_by_config)
    assert periods == ["20250102"]
    assert matrix == [[10.0, 3.0]]


def test_build_matrix_feeds_compute_pbo_cscv():
    # dominant config가 매 기간 우월 → 조립된 행렬로 PBO 0
    records_by_config = {}
    for c in range(4):
        recs = []
        for d in range(40):
            pnl = (100.0 if c == 0 else -5.0) + (1.0 if d % 2 else 0.0)
            recs.append({"status": "SOLD", "signal_time": f"2025{(d // 28) + 1:02d}{(d % 28) + 1:02d}", "net_pnl": pnl})
        records_by_config[f"c{c}"] = recs
    matrix, configs, periods = build_config_period_pnl_matrix(records_by_config)
    res = compute_pbo_cscv(matrix, n_splits=8)
    assert res["available"] is True
    assert res["n_configs"] == 4
    assert res["pbo"] == 0.0


def test_summary_pbo_cscv_absent_when_no_matrix():
    summary = compute_multiple_testing_bias_summary(
        {"S1": {"total_net_pnl": 100}, "S2": {"total_net_pnl": 50}},
    )
    assert summary["pbo_cscv"]["available"] is False
    assert summary["pbo_cscv"]["reason"] == "not_provided"
    assert "pbo_cscv_above_threshold" not in summary["warning_reasons"]


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


def test_multiple_testing_bias_summary_reports_formal_deflated_sharpe():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"total_net_pnl": 1000, "sharpe_ratio": 2.2, "trade_count": 100},
            "S2": {"total_net_pnl": 800, "sharpe_ratio": 1.8, "trade_count": 100},
            "S3": {"total_net_pnl": 600, "sharpe_ratio": 1.2, "trade_count": 100},
            "S4": {"total_net_pnl": 400, "sharpe_ratio": 0.9, "trade_count": 100},
            "S5": {"total_net_pnl": 200, "sharpe_ratio": 0.7, "trade_count": 100},
        },
        min_trials=5,
    )

    dsr = summary["deflated_sharpe"]
    assert dsr["available"] is True
    assert dsr["best_strategy"] == "S1"
    assert dsr["best_sharpe"] == 2.2
    assert dsr["trial_count"] == 5
    assert dsr["sample_size"] == 100
    # Expected max Sharpe under the null (trials-adjusted) is deterministic.
    assert dsr["expected_max_sharpe"] == pytest.approx(0.7476, abs=0.01)
    # Deflated Sharpe is a probability in [0, 1]; strong edge + large sample -> ~1.
    assert 0.0 <= dsr["deflated_sharpe_ratio"] <= 1.0
    assert dsr["deflated_sharpe_ratio"] > 0.99
    # No threshold -> never a warning.
    assert dsr["passed"] is True
    assert "deflated_sharpe_probability_below_threshold" not in summary["warning_reasons"]


def test_formal_deflated_sharpe_warns_when_probability_below_threshold():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"sharpe_ratio": 1.0, "trade_count": 10, "total_net_pnl": 50},
            "S2": {"sharpe_ratio": 0.2, "trade_count": 10, "total_net_pnl": 10},
            "S3": {"sharpe_ratio": 0.1, "trade_count": 10, "total_net_pnl": 8},
            "S4": {"sharpe_ratio": 0.0, "trade_count": 10, "total_net_pnl": 6},
            "S5": {"sharpe_ratio": -0.1, "trade_count": 10, "total_net_pnl": 4},
        },
        min_trials=5,
        min_deflated_sharpe_probability=0.95,
    )

    dsr = summary["deflated_sharpe"]
    assert dsr["available"] is True
    assert dsr["best_strategy"] == "S1"
    assert 0.0 < dsr["deflated_sharpe_ratio"] < 0.95
    assert dsr["passed"] is False
    assert "deflated_sharpe_probability_below_threshold" in summary["warning_reasons"]
    assert summary["bias_warning"] is True


def test_formal_deflated_sharpe_unavailable_without_sample_size():
    summary = compute_multiple_testing_bias_summary(
        {
            "S1": {"sharpe_ratio": 2.2, "total_net_pnl": 1000},
            "S2": {"sharpe_ratio": 1.8, "total_net_pnl": 800},
            "S3": {"sharpe_ratio": 1.2, "total_net_pnl": 600},
            "S4": {"sharpe_ratio": 0.9, "total_net_pnl": 400},
            "S5": {"sharpe_ratio": 0.7, "total_net_pnl": 200},
        },
        min_trials=5,
        min_deflated_sharpe_probability=0.95,
    )

    dsr = summary["deflated_sharpe"]
    assert dsr["available"] is False
    assert dsr["passed"] is True
    assert "deflated_sharpe_probability_below_threshold" not in summary["warning_reasons"]


def test_formal_deflated_sharpe_uses_skew_and_kurtosis_when_present():
    base = {
        "S1": {"sharpe_ratio": 2.2, "trade_count": 20, "total_net_pnl": 1000},
        "S2": {"sharpe_ratio": 1.8, "trade_count": 20, "total_net_pnl": 800},
        "S3": {"sharpe_ratio": 1.2, "trade_count": 20, "total_net_pnl": 600},
        "S4": {"sharpe_ratio": 0.9, "trade_count": 20, "total_net_pnl": 400},
        "S5": {"sharpe_ratio": 0.7, "trade_count": 20, "total_net_pnl": 200},
    }
    # Negative skew + fat tails inflate the PSR denominator -> lower DSR than normal.
    fat = {k: dict(v) for k, v in base.items()}
    fat["S1"].update({"return_skew": -1.5, "return_kurtosis": 9.0})

    normal_summary = compute_multiple_testing_bias_summary(base, min_trials=5)
    fat_summary = compute_multiple_testing_bias_summary(fat, min_trials=5)

    assert (
        fat_summary["deflated_sharpe"]["deflated_sharpe_ratio"]
        < normal_summary["deflated_sharpe"]["deflated_sharpe_ratio"]
    )
