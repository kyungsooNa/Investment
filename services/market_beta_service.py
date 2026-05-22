from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def compute_market_beta_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    min_overlap: int = 5,
    warning_threshold: float = 1.5,
    metric: str = "net_return",
    benchmark_metric: str = "market_return",
) -> dict[str, Any]:
    series_by_strategy, benchmark_series = _daily_series(records, metric, benchmark_metric)
    portfolio_series: dict[str, float] = {}
    for strategy_series in series_by_strategy.values():
        for date, value in strategy_series.items():
            portfolio_series[date] = portfolio_series.get(date, 0.0) + value

    portfolio = _beta_payload(portfolio_series, benchmark_series, min_overlap=min_overlap)
    strategies = {
        strategy: _beta_payload(series, benchmark_series, min_overlap=min_overlap)
        for strategy, series in sorted(series_by_strategy.items())
    }
    high_beta_strategies = [
        {
            "strategy": strategy,
            "beta": payload["beta"],
            "overlap": payload["overlap"],
        }
        for strategy, payload in strategies.items()
        if payload["status"] == "ok"
        and payload["beta"] is not None
        and payload["beta"] >= warning_threshold
    ]
    high_beta_strategies.sort(key=lambda item: (-float(item["beta"]), item["strategy"]))

    warnings: list[str] = []
    if (
        portfolio["status"] == "ok"
        and portfolio["beta"] is not None
        and portfolio["beta"] >= warning_threshold
    ):
        warnings.append("portfolio_market_beta_high")
    if high_beta_strategies:
        warnings.append("strategy_market_beta_high")

    return {
        "metric": metric,
        "benchmark_metric": benchmark_metric,
        "min_overlap": min_overlap,
        "warning_threshold": warning_threshold,
        "portfolio": portfolio,
        "strategies": strategies,
        "high_beta_strategies": high_beta_strategies,
        "warnings": warnings,
    }


def _daily_series(
    records: Iterable[Mapping[str, Any]],
    metric: str,
    benchmark_metric: str,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    series_by_strategy: dict[str, dict[str, float]] = {}
    benchmark_series: dict[str, float] = {}
    for record in records:
        if str(record.get("status") or "").upper() != "SOLD":
            continue
        strategy = str(record.get("strategy") or "").strip()
        date = _record_date(record)
        strategy_return = _to_float(record.get(metric))
        benchmark_return = _record_benchmark_return(record, benchmark_metric)
        if not strategy or not date or strategy_return is None or benchmark_return is None:
            continue
        strategy_series = series_by_strategy.setdefault(strategy, {})
        strategy_series[date] = strategy_series.get(date, 0.0) + strategy_return
        benchmark_series.setdefault(date, benchmark_return)
    return series_by_strategy, benchmark_series


def _beta_payload(
    series: Mapping[str, float],
    benchmark_series: Mapping[str, float],
    *,
    min_overlap: int,
) -> dict[str, Any]:
    overlap_dates = sorted(set(series) & set(benchmark_series))
    overlap = len(overlap_dates)
    if overlap < min_overlap:
        return {"status": "insufficient_sample", "beta": None, "overlap": overlap}

    values = [series[date] for date in overlap_dates]
    benchmark_values = [benchmark_series[date] for date in overlap_dates]
    beta = _beta(values, benchmark_values)
    if beta is None:
        return {"status": "zero_benchmark_variance", "beta": None, "overlap": overlap}
    return {"status": "ok", "beta": beta, "overlap": overlap}


def _record_benchmark_return(record: Mapping[str, Any], benchmark_metric: str) -> float | None:
    for key in (
        benchmark_metric,
        "market_return",
        "benchmark_return",
        "kospi_return",
        "kosdaq_return",
    ):
        value = _to_float(record.get(key))
        if value is not None:
            return value
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        for key in (
            benchmark_metric,
            "market_return",
            "benchmark_return",
            "kospi_return",
            "kosdaq_return",
        ):
            value = _to_float(metadata.get(key))
            if value is not None:
                return value
    return None


def _record_date(record: Mapping[str, Any]) -> str:
    raw = str(record.get("signal_time") or record.get("date") or "").strip()
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10] if raw else ""


def _beta(values: list[float], benchmark_values: list[float]) -> float | None:
    if len(values) != len(benchmark_values) or len(values) < 2:
        return None
    avg_value = sum(values) / len(values)
    avg_benchmark = sum(benchmark_values) / len(benchmark_values)
    value_diffs = [value - avg_value for value in values]
    benchmark_diffs = [value - avg_benchmark for value in benchmark_values]
    variance = sum(value * value for value in benchmark_diffs)
    if math.isclose(variance, 0.0):
        return None
    covariance = sum(value * benchmark for value, benchmark in zip(value_diffs, benchmark_diffs))
    return round(covariance / variance, 6)


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
