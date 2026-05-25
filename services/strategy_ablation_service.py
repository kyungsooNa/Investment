"""Strategy ablation analysis.

Pure helpers that drive ablation backtests: building variant configs by
overriding fields on a dataclass, wrapping the universe service to neutralize
market-timing gates, and aggregating per-variant trade metrics into a
comparison summary.

The caller (e.g. ``scripts.run_backtest``) is responsible for running the
backtest itself; this module never touches brokers, schedulers, or files.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from services.strategy_performance_degradation_service import (
    compute_strategy_window_metrics,
)


_BASELINE_KEY = "baseline"


@dataclass(frozen=True)
class AblationVariant:
    """A single ablation variant — what to override and why."""

    name: str
    description: str = ""
    config_overrides: Mapping[str, Any] = field(default_factory=dict)
    universe_overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AblationPreset:
    """An ordered set of variants for one strategy."""

    strategy_key: str
    variants: tuple[AblationVariant, ...]

    def __post_init__(self) -> None:
        if not self.variants:
            raise ValueError("AblationPreset requires at least one variant.")
        names = [v.name for v in self.variants]
        if _BASELINE_KEY in names:
            raise ValueError(
                f"AblationVariant name '{_BASELINE_KEY}' is reserved for the baseline run."
            )
        if len(set(names)) != len(names):
            raise ValueError(f"AblationPreset has duplicate variant names: {names}")

    def variant_names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variants)


def apply_config_overrides(config_obj: Any, overrides: Mapping[str, Any]) -> Any:
    """Return a new dataclass copy with the given fields overridden.

    Unknown fields raise ``ValueError`` so a typo in a preset surfaces loudly
    instead of silently affecting nothing.
    """
    if not isinstance(overrides, Mapping):
        raise TypeError(
            f"overrides must be a Mapping, got {type(overrides).__name__}"
        )
    if not overrides:
        return dataclasses.replace(config_obj)

    valid_fields = {f.name for f in dataclasses.fields(config_obj)}
    unknown = sorted(set(overrides.keys()) - valid_fields)
    if unknown:
        raise ValueError(
            f"Unknown config field(s) for {type(config_obj).__name__}: {unknown}"
        )
    return dataclasses.replace(config_obj, **dict(overrides))


class ForceMarketTimingOkUniverseWrapper:
    """Universe-service wrapper that forces ``is_market_timing_ok`` to True.

    Used by ablation runs that need to neutralize the market-timing gate while
    keeping every other universe behavior (watchlist, subscription policy,
    candidate filtering) intact. All other attribute access is delegated to
    the wrapped instance.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def is_market_timing_ok(
        self, market: str, caller: str = "", logger: Any = None
    ) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def compute_ablation_summary(
    *,
    baseline_records: Iterable[Mapping[str, Any]],
    variant_records: Mapping[str, Iterable[Mapping[str, Any]]],
    capital_base_won: Optional[float] = None,
) -> dict[str, Any]:
    """Return baseline metrics, per-variant metrics, and deltas vs baseline."""
    baseline_metrics = _compute_metrics(baseline_records, capital_base_won)

    variants: dict[str, dict[str, Any]] = {}
    for variant_name, records in variant_records.items():
        variant_metrics = _compute_metrics(records, capital_base_won)
        variants[variant_name] = {
            "metrics": variant_metrics,
            "delta": _compute_delta(baseline_metrics, variant_metrics),
        }

    return {
        "capital_base_won": capital_base_won,
        "baseline": {"metrics": baseline_metrics},
        "variants": variants,
        "variant_count": len(variants),
    }


def compute_ablation_gate_summary(
    summary: Mapping[str, Any],
    *,
    max_variant_outperformance_pct: Optional[float] = None,
) -> dict[str, Any]:
    baseline_metrics = (summary.get("baseline") or {}).get("metrics") or {}
    baseline_pnl = _to_float(baseline_metrics.get("total_net_pnl")) or 0.0
    threshold = (
        None
        if max_variant_outperformance_pct is None
        else float(max_variant_outperformance_pct)
    )
    violations: list[dict[str, Any]] = []

    for variant_name, payload in (summary.get("variants") or {}).items():
        delta = payload.get("delta") or {}
        pnl_diff = _to_float(delta.get("total_net_pnl_diff"))
        if pnl_diff is None:
            variant_metrics = payload.get("metrics") or {}
            variant_pnl = _to_float(variant_metrics.get("total_net_pnl")) or 0.0
            pnl_diff = variant_pnl - baseline_pnl
        outperformance_pct = _outperformance_pct(pnl_diff, baseline_pnl)
        if threshold is not None and pnl_diff > 0 and outperformance_pct > threshold:
            violations.append(
                {
                    "variant": str(variant_name),
                    "total_net_pnl_diff": pnl_diff,
                    "outperformance_pct": outperformance_pct,
                    "threshold_pct": threshold,
                }
            )

    violations.sort(
        key=lambda item: (
            float(item.get("outperformance_pct") or 0.0),
            float(item.get("total_net_pnl_diff") or 0.0),
        ),
        reverse=True,
    )
    return {
        "passed": not violations,
        "blocking_reasons": (
            ["ablation_variant_outperforms_baseline"] if violations else []
        ),
        "max_variant_outperformance_pct": threshold,
        "worst_variant": violations[0] if violations else None,
        "violations": violations,
    }


def _compute_metrics(
    records: Iterable[Mapping[str, Any]],
    capital_base_won: Optional[float],
) -> dict[str, Any]:
    sold = [
        record
        for record in records
        if str(record.get("status") or "").upper() == "SOLD"
    ]
    if not sold:
        return _empty_metrics()

    # Group everything under a single synthetic key — compute_strategy_window_metrics
    # buckets by record["strategy"], so we normalize to one bucket here.
    flat_records = [dict(record, strategy="__ablation__") for record in sold]
    by_strategy = compute_strategy_window_metrics(
        flat_records,
        window_size=max(len(flat_records), 1),
        capital_base_won=capital_base_won,
    )
    metrics = by_strategy.get("__ablation__", _empty_metrics())
    return dict(metrics)


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_net_return": 0.0,
        "total_net_pnl": 0.0,
        "payoff_ratio": None,
        "profit_factor": None,
        "mdd_amount": 0.0,
        "mdd_ratio": None,
        "max_consecutive_losses": 0,
        "avg_mfe": None,
        "avg_mae": None,
    }


def _compute_delta(
    baseline: Mapping[str, Any], variant: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "trade_count_diff": int(variant["trade_count"]) - int(baseline["trade_count"]),
        "win_rate_diff": float(variant["win_rate"]) - float(baseline["win_rate"]),
        "avg_net_return_diff": float(variant["avg_net_return"])
        - float(baseline["avg_net_return"]),
        "total_net_pnl_diff": float(variant["total_net_pnl"])
        - float(baseline["total_net_pnl"]),
        "profit_factor_diff": _optional_diff(
            variant.get("profit_factor"), baseline.get("profit_factor")
        ),
        "payoff_ratio_diff": _optional_diff(
            variant.get("payoff_ratio"), baseline.get("payoff_ratio")
        ),
        "mdd_amount_diff": float(variant["mdd_amount"]) - float(baseline["mdd_amount"]),
    }


def _optional_diff(left: Any, right: Any) -> Optional[float]:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _outperformance_pct(pnl_diff: float, baseline_pnl: float) -> float:
    denominator = abs(baseline_pnl)
    if denominator == 0:
        return float("inf") if pnl_diff > 0 else 0.0
    return (pnl_diff / denominator) * 100.0


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_universe_exclusion_summary(
    *,
    baseline_records: Iterable[Mapping[str, Any]],
    variant_records: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compute baseline-vs-variant traded-code set difference per variant.

    SOLD records 만 대상이며, ``code`` 필드가 없는 레코드는 무시한다. universe
    ablation 비교 시 baseline universe 외부에서 variant universe 가 잡은 종목과
    그 PnL 합을 한눈에 보기 위한 리포트.
    """
    baseline_sold = _filter_sold_with_code(baseline_records)
    baseline_codes = sorted({r["code"] for r in baseline_sold})
    baseline_set = set(baseline_codes)

    variants_report: dict[str, dict[str, Any]] = {}
    for variant_name, records in variant_records.items():
        variant_sold = _filter_sold_with_code(records)
        variant_codes = {r["code"] for r in variant_sold}

        variant_only = sorted(variant_codes - baseline_set)
        baseline_only = sorted(baseline_set - variant_codes)
        shared = sorted(baseline_set & variant_codes)

        variant_only_summary = _summarize_variant_only_records(
            variant_sold, variant_only_codes=set(variant_only)
        )

        variants_report[variant_name] = {
            "variant_only_codes": variant_only,
            "baseline_only_codes": baseline_only,
            "shared_codes": shared,
            "variant_only_summary": variant_only_summary,
        }

    return {
        "baseline_codes": baseline_codes,
        "variants": variants_report,
    }


def _filter_sold_with_code(
    records: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for record in records:
        if str(record.get("status") or "").upper() != "SOLD":
            continue
        code = record.get("code")
        if not code:
            continue
        result.append(record)
    return result


def _summarize_variant_only_records(
    sold_records: Iterable[Mapping[str, Any]],
    *,
    variant_only_codes: set[str],
) -> dict[str, Any]:
    if not variant_only_codes:
        return {
            "trade_count": 0,
            "total_net_pnl": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "per_code": {},
        }

    per_code: dict[str, dict[str, Any]] = {}
    trade_count = 0
    total_pnl = 0.0
    win_count = 0
    loss_count = 0

    for record in sold_records:
        code = record["code"]
        if code not in variant_only_codes:
            continue
        net_pnl = float(record.get("net_pnl") or 0.0)
        signal_time = str(record.get("signal_time") or "")

        trade_count += 1
        total_pnl += net_pnl
        if net_pnl > 0:
            win_count += 1
        elif net_pnl < 0:
            loss_count += 1

        bucket = per_code.setdefault(
            code,
            {
                "trade_count": 0,
                "total_net_pnl": 0.0,
                "first_signal_time": signal_time,
                "last_signal_time": signal_time,
            },
        )
        bucket["trade_count"] += 1
        bucket["total_net_pnl"] += net_pnl
        if signal_time and signal_time < bucket["first_signal_time"]:
            bucket["first_signal_time"] = signal_time
        if signal_time and signal_time > bucket["last_signal_time"]:
            bucket["last_signal_time"] = signal_time

    return {
        "trade_count": trade_count,
        "total_net_pnl": total_pnl,
        "win_count": win_count,
        "loss_count": loss_count,
        "per_code": per_code,
    }
