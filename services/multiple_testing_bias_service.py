"""Lightweight multiple-testing bias report helpers.

This is a report-only proxy, not a formal Deflated Sharpe or PBO
implementation. It flags cases where many strategy trials are compared and the
best result towers over the median result, which can indicate selection bias.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Mapping


def compute_multiple_testing_bias_summary(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    min_trials: int = 5,
    top_to_median_warning_ratio: float = 3.0,
    primary_metric: str = "total_net_pnl",
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
    }


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
