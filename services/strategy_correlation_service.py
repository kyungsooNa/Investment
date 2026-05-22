from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Iterable, Mapping


def compute_strategy_correlation_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    min_overlap: int = 5,
    warning_threshold: float = 0.8,
    metric: str = "net_return",
) -> dict[str, Any]:
    """Compute pairwise strategy correlation from standard SOLD journal records."""
    series_by_strategy = _daily_series_by_strategy(records, metric=metric)
    pairs: list[dict[str, Any]] = []
    skipped_pairs: list[dict[str, Any]] = []

    for left, right in combinations(sorted(series_by_strategy), 2):
        left_series = series_by_strategy[left]
        right_series = series_by_strategy[right]
        overlap_dates = sorted(set(left_series) & set(right_series))
        if len(overlap_dates) < min_overlap:
            skipped_pairs.append({
                "left": left,
                "right": right,
                "reason": "insufficient_overlap",
                "overlap": len(overlap_dates),
            })
            continue

        correlation = _pearson(
            [left_series[date] for date in overlap_dates],
            [right_series[date] for date in overlap_dates],
        )
        if correlation is None:
            skipped_pairs.append({
                "left": left,
                "right": right,
                "reason": "zero_variance",
                "overlap": len(overlap_dates),
            })
            continue
        pairs.append({
            "left": left,
            "right": right,
            "correlation": correlation,
            "overlap": len(overlap_dates),
        })

    high_pairs = [
        pair for pair in pairs
        if pair["correlation"] >= warning_threshold
    ]
    high_pairs.sort(key=lambda pair: (-pair["correlation"], pair["left"], pair["right"]))
    max_positive_pair = max(
        pairs,
        key=lambda pair: pair["correlation"],
        default=None,
    )
    return {
        "metric": metric,
        "min_overlap": min_overlap,
        "warning_threshold": warning_threshold,
        "strategy_count": len(series_by_strategy),
        "pair_count": len(pairs),
        "pairs": pairs,
        "skipped_pairs": skipped_pairs,
        "high_correlation_pairs": high_pairs,
        "max_positive_pair": max_positive_pair,
        "warnings": ["strategy_correlation_high"] if high_pairs else [],
    }


def _daily_series_by_strategy(
    records: Iterable[Mapping[str, Any]],
    *,
    metric: str,
) -> dict[str, dict[str, float]]:
    series: dict[str, dict[str, float]] = {}
    for record in records:
        if str(record.get("status") or "").upper() != "SOLD":
            continue
        strategy = str(record.get("strategy") or "").strip()
        date = _record_date(record)
        if not strategy or not date:
            continue
        value = _to_float(record.get(metric))
        if value is None and metric != "net_pnl":
            value = _to_float(record.get("net_pnl"))
        if value is None:
            continue
        strategy_series = series.setdefault(strategy, {})
        strategy_series[date] = strategy_series.get(date, 0.0) + value
    return series


def _record_date(record: Mapping[str, Any]) -> str:
    raw = str(record.get("signal_time") or record.get("date") or "").strip()
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10] if raw else ""


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_avg = sum(left) / len(left)
    right_avg = sum(right) / len(right)
    left_diffs = [value - left_avg for value in left]
    right_diffs = [value - right_avg for value in right]
    numerator = sum(l * r for l, r in zip(left_diffs, right_diffs))
    left_denominator = math.sqrt(sum(value * value for value in left_diffs))
    right_denominator = math.sqrt(sum(value * value for value in right_diffs))
    if left_denominator == 0 or right_denominator == 0:
        return None
    return round(numerator / (left_denominator * right_denominator), 6)


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
