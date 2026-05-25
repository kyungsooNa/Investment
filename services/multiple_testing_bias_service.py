"""Lightweight multiple-testing bias report helpers.

This module exposes conservative proxies, not formal Deflated Sharpe or PBO
implementations. They are intended to turn strategy-selection red flags into a
stable gate contract without requiring a full walk-forward research stack.
"""
from __future__ import annotations

import math
from statistics import median, pstdev
from typing import Any, Mapping


def compute_multiple_testing_bias_summary(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    min_trials: int = 5,
    top_to_median_warning_ratio: float = 3.0,
    primary_metric: str = "total_net_pnl",
    min_adjusted_sharpe: float | None = None,
    max_pbo_probability: float | None = None,
    sharpe_metric: str = "sharpe_ratio",
    in_sample_metric: str = "in_sample_net_pnl",
    out_of_sample_metric: str = "out_of_sample_net_pnl",
) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        value = _to_float(metrics.get(primary_metric))
        if value is None:
            continue
        rows.append((str(strategy), value))

    rows.sort(key=lambda item: item[1], reverse=True)
    trial_count = len(rows)
    warning_reasons: list[str] = []

    best_strategy = rows[0][0] if rows else None
    best_value = rows[0][1] if rows else None
    median_value = float(median([value for _, value in rows])) if rows else None
    ratio = None

    if trial_count >= max(int(min_trials or 0), 1) and best_value is not None:
        if median_value is not None and median_value > 0:
            ratio = best_value / median_value
            if ratio >= float(top_to_median_warning_ratio):
                warning_reasons.append("best_over_median_ratio_high")
        elif best_value > 0:
            warning_reasons.append("best_positive_median_non_positive")

    deflated_sharpe = _compute_deflated_sharpe_proxy(
        metrics_by_strategy,
        trial_count=trial_count,
        min_trials=min_trials,
        threshold=min_adjusted_sharpe,
        metric=sharpe_metric,
    )
    if deflated_sharpe.get("available") and deflated_sharpe.get("passed") is False:
        warning_reasons.append("deflated_sharpe_below_threshold")

    pbo = _compute_pbo_proxy(
        metrics_by_strategy,
        min_trials=min_trials,
        threshold=max_pbo_probability,
        in_sample_metric=in_sample_metric,
        out_of_sample_metric=out_of_sample_metric,
    )
    if pbo.get("available") and pbo.get("passed") is False:
        warning_reasons.append("pbo_probability_above_threshold")

    return {
        "trial_count": trial_count,
        "primary_metric": primary_metric,
        "best_strategy": best_strategy,
        "best_value": best_value,
        "median_value": median_value,
        "top_to_median_ratio": round(ratio, 3) if ratio is not None else None,
        "warning_reasons": warning_reasons,
        "bias_warning": bool(warning_reasons),
        "rankings": [
            {"strategy": strategy, primary_metric: value}
            for strategy, value in rows
        ],
        "deflated_sharpe_proxy": deflated_sharpe,
        "pbo_proxy": pbo,
    }


def _compute_deflated_sharpe_proxy(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    trial_count: int,
    min_trials: int,
    threshold: float | None,
    metric: str,
) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        value = _to_float(metrics.get(metric))
        if value is not None:
            rows.append((str(strategy), value))

    rows.sort(key=lambda item: item[1], reverse=True)
    if len(rows) < max(int(min_trials or 0), 1):
        return {
            "available": False,
            "metric": metric,
            "sample_count": len(rows),
            "trial_count": trial_count,
            "threshold": threshold,
            "passed": True,
        }

    best_strategy, best_sharpe = rows[0]
    sharpe_values = [value for _, value in rows]
    dispersion = pstdev(sharpe_values) if len(sharpe_values) > 1 else 0.0
    selection_haircut = math.sqrt(2.0 * math.log(max(len(rows), 1))) * dispersion
    adjusted_sharpe = best_sharpe - selection_haircut
    passed = True if threshold is None else adjusted_sharpe >= float(threshold)

    return {
        "available": True,
        "metric": metric,
        "sample_count": len(rows),
        "trial_count": trial_count,
        "best_strategy": best_strategy,
        "best_sharpe": round(best_sharpe, 6),
        "adjusted_sharpe": round(adjusted_sharpe, 6),
        "selection_haircut": round(selection_haircut, 6),
        "threshold": threshold,
        "passed": passed,
    }


def _compute_pbo_proxy(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    min_trials: int,
    threshold: float | None,
    in_sample_metric: str,
    out_of_sample_metric: str,
) -> dict[str, Any]:
    rows: list[tuple[str, float, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        in_sample = _to_float(metrics.get(in_sample_metric))
        out_of_sample = _to_float(metrics.get(out_of_sample_metric))
        if in_sample is not None and out_of_sample is not None:
            rows.append((str(strategy), in_sample, out_of_sample))

    rows.sort(key=lambda item: item[1], reverse=True)
    if len(rows) < max(int(min_trials or 0), 1):
        return {
            "available": False,
            "in_sample_metric": in_sample_metric,
            "out_of_sample_metric": out_of_sample_metric,
            "sample_count": len(rows),
            "threshold": threshold,
            "passed": True,
        }

    top_count = max(len(rows) // 2, 1)
    top_rows = rows[:top_count]
    out_of_sample_median = float(median([out_sample for _, _, out_sample in rows]))
    failed_top_count = sum(
        1
        for _, _, out_sample in top_rows
        if out_sample <= out_of_sample_median or out_sample <= 0
    )
    pbo_probability = failed_top_count / len(top_rows)
    passed = True if threshold is None else pbo_probability <= float(threshold)

    return {
        "available": True,
        "in_sample_metric": in_sample_metric,
        "out_of_sample_metric": out_of_sample_metric,
        "sample_count": len(rows),
        "top_in_sample_strategies": [strategy for strategy, _, _ in top_rows],
        "out_of_sample_median": round(out_of_sample_median, 6),
        "pbo_probability": round(pbo_probability, 6),
        "threshold": threshold,
        "passed": passed,
    }


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
