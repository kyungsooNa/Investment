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
