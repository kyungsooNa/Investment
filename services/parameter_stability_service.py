"""Parameter stability surface analysis.

Pure helpers that drive parameter-stability sweeps: build per-dimension
metric tables from baseline + sweep journal records, and classify each
dimension as ``stable`` / ``spike`` / ``cliff`` / ``edge`` so an operator can
spot single-threshold performance peaks.

The caller (e.g. ``scripts.run_backtest``) is responsible for running each
sweep point's backtest itself; this module never touches brokers,
schedulers, or files. See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from services.strategy_performance_degradation_service import (
    compute_strategy_window_metrics,
)


PRIMARY_METRIC = "total_net_pnl"

# Classification thresholds (documented in plan §1):
_CLIFF_DROP_RATIO = 0.80    # neighbor pnl drops >= 80% of baseline → cliff
_SPIKE_DROP_RATIO = 0.50    # both neighbors drop >= 50% of baseline → spike candidate
_SPIKE_RATIO = 2.0          # baseline / mean(neighbors) >= 2 → spike


@dataclass(frozen=True)
class StabilitySweepDimension:
    """A single parameter sweep — one config field, N values around baseline.

    ``values`` must be sorted in ascending order and contain at least three
    points. ``baseline_index`` points to the value that matches the strategy's
    current default (so the sweep is centered on the live config).
    """

    name: str
    parameter: str
    values: tuple[Any, ...]
    baseline_index: int
    description: str = ""

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError(
                f"StabilitySweepDimension '{self.name}': values must not be empty."
            )
        if len(self.values) < 3:
            raise ValueError(
                f"StabilitySweepDimension '{self.name}': needs at least 3 values "
                f"(left + baseline + right) for spike classification."
            )
        if not (0 <= self.baseline_index < len(self.values)):
            raise ValueError(
                f"StabilitySweepDimension '{self.name}': baseline_index "
                f"{self.baseline_index} out of range for values length {len(self.values)}."
            )

    @property
    def baseline_value(self) -> Any:
        return self.values[self.baseline_index]

    @property
    def value_count(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class StabilitySweepPreset:
    """An ordered set of dimensions for one strategy."""

    strategy_key: str
    dimensions: tuple[StabilitySweepDimension, ...]

    def __post_init__(self) -> None:
        if not self.dimensions:
            raise ValueError(
                f"StabilitySweepPreset '{self.strategy_key}' requires at least one dimension."
            )
        names = [d.name for d in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError(
                f"StabilitySweepPreset '{self.strategy_key}' has duplicate dimension names: {names}"
            )

    def dimension_names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.dimensions)


def compute_stability_summary(
    baseline_records: Iterable[Mapping[str, Any]],
    dimensions: Sequence[StabilitySweepDimension],
    sweep_records_by_dim: Mapping[str, Mapping[Any, Iterable[Mapping[str, Any]]]],
    *,
    capital_base_won: Optional[float] = None,
) -> dict[str, Any]:
    """Build the parameter-stability surface and per-dimension classification.

    See module docstring for output shape.
    """
    baseline_metrics = _compute_metrics(baseline_records, capital_base_won)

    dim_payloads: dict[str, Any] = {}
    for dim in dimensions:
        if dim.name not in sweep_records_by_dim:
            raise KeyError(
                f"sweep_records_by_dim is missing dimension '{dim.name}'."
            )
        dim_payloads[dim.name] = _compute_dimension_payload(
            dim,
            sweep_records_by_dim[dim.name],
            baseline_metrics,
            capital_base_won,
        )

    return {
        "capital_base_won": capital_base_won,
        "baseline": {"metrics": baseline_metrics},
        "dimensions": dim_payloads,
        "dimension_count": len(dim_payloads),
    }


def _compute_dimension_payload(
    dim: StabilitySweepDimension,
    sweep_records_for_dim: Mapping[Any, Iterable[Mapping[str, Any]]],
    baseline_metrics: Mapping[str, Any],
    capital_base_won: Optional[float],
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for idx, value in enumerate(dim.values):
        if value not in sweep_records_for_dim:
            raise KeyError(
                f"sweep_records_by_dim['{dim.name}'] is missing value {value!r}."
            )
        records = sweep_records_for_dim[value]
        metrics = _compute_metrics(records, capital_base_won)
        delta = _compute_delta(baseline_metrics, metrics)
        points.append(
            {
                "value": value,
                "metrics": metrics,
                "delta": delta,
                "is_baseline": idx == dim.baseline_index,
            }
        )

    stability = _classify_stability(dim, points)
    return {
        "parameter": dim.parameter,
        "baseline_value": dim.baseline_value,
        "description": dim.description,
        "points": points,
        "stability": stability,
    }


def _classify_stability(
    dim: StabilitySweepDimension,
    points: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_idx = dim.baseline_index
    baseline_pnl = float(points[baseline_idx]["metrics"].get(PRIMARY_METRIC, 0.0))

    # baseline must be at an interior point AND positive to make spike/cliff meaningful
    if baseline_idx == 0 or baseline_idx == len(points) - 1:
        return {
            "flag": "edge",
            "primary_metric": PRIMARY_METRIC,
            "primary_value_at_baseline": baseline_pnl,
            "ratio_vs_neighbors_avg": None,
            "neighbor_drop_pct": None,
            "reason": "baseline_at_sweep_edge",
        }

    if baseline_pnl <= 0:
        return {
            "flag": "stable",
            "primary_metric": PRIMARY_METRIC,
            "primary_value_at_baseline": baseline_pnl,
            "ratio_vs_neighbors_avg": None,
            "neighbor_drop_pct": None,
            "reason": "baseline_non_positive",
        }

    left_pnl = float(points[baseline_idx - 1]["metrics"].get(PRIMARY_METRIC, 0.0))
    right_pnl = float(points[baseline_idx + 1]["metrics"].get(PRIMARY_METRIC, 0.0))

    left_drop_ratio = _drop_ratio(baseline_pnl, left_pnl)
    right_drop_ratio = _drop_ratio(baseline_pnl, right_pnl)
    max_drop_ratio = max(left_drop_ratio, right_drop_ratio)

    # cliff: at least one neighbor sign-flipped or dropped >= 80%
    if left_pnl <= 0 or right_pnl <= 0 or max_drop_ratio >= _CLIFF_DROP_RATIO:
        return {
            "flag": "cliff",
            "primary_metric": PRIMARY_METRIC,
            "primary_value_at_baseline": baseline_pnl,
            "ratio_vs_neighbors_avg": _ratio_vs_neighbors(baseline_pnl, left_pnl, right_pnl),
            "neighbor_drop_pct": round(max_drop_ratio * 100.0, 2),
            "reason": "neighbor_sign_flip_or_steep_drop",
        }

    # spike: both neighbors drop >= 50% AND baseline tower at >= 2x avg-neighbor
    neighbors_mean = (left_pnl + right_pnl) / 2.0
    ratio = baseline_pnl / neighbors_mean if neighbors_mean > 0 else None
    if (
        left_drop_ratio >= _SPIKE_DROP_RATIO
        and right_drop_ratio >= _SPIKE_DROP_RATIO
        and ratio is not None
        and ratio >= _SPIKE_RATIO
    ):
        return {
            "flag": "spike",
            "primary_metric": PRIMARY_METRIC,
            "primary_value_at_baseline": baseline_pnl,
            "ratio_vs_neighbors_avg": round(ratio, 3),
            "neighbor_drop_pct": round(max_drop_ratio * 100.0, 2),
            "reason": "single_point_peak",
        }

    return {
        "flag": "stable",
        "primary_metric": PRIMARY_METRIC,
        "primary_value_at_baseline": baseline_pnl,
        "ratio_vs_neighbors_avg": round(ratio, 3) if ratio is not None else None,
        "neighbor_drop_pct": round(max_drop_ratio * 100.0, 2),
        "reason": "neighbors_within_tolerance",
    }


def _drop_ratio(baseline: float, neighbor: float) -> float:
    """Fraction of baseline lost at this neighbor (0.0 = same, 1.0 = lost all)."""
    if baseline <= 0:
        return 0.0
    return max(0.0, (baseline - neighbor) / baseline)


def _ratio_vs_neighbors(baseline: float, left: float, right: float) -> Optional[float]:
    mean = (left + right) / 2.0
    if mean <= 0:
        return None
    return round(baseline / mean, 3)


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

    flat_records = [dict(record, strategy="__stability__") for record in sold]
    by_strategy = compute_strategy_window_metrics(
        flat_records,
        window_size=max(len(flat_records), 1),
        capital_base_won=capital_base_won,
    )
    metrics = by_strategy.get("__stability__", _empty_metrics())
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
