from __future__ import annotations

import pytest

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
    compute_stability_summary,
)


def _sold(pnl: float, ret: float, *, signal_time: str = "2026-05-01") -> dict:
    return {
        "status": "SOLD",
        "strategy": "S",
        "net_pnl": pnl,
        "net_return": ret,
        "signal_time": signal_time,
    }


def _dim_pp() -> StabilitySweepDimension:
    return StabilitySweepDimension(
        name="pp_ma_proximity_upper_pct",
        parameter="pp_ma_proximity_upper_pct",
        values=(2.0, 3.0, 4.0, 5.0, 6.0),
        baseline_index=2,
        description="PP MA proximity 상한 sweep.",
    )


class TestStabilitySweepDimensionValidation:
    def test_rejects_empty_values(self):
        with pytest.raises(ValueError, match="values"):
            StabilitySweepDimension(
                name="x", parameter="x", values=(), baseline_index=0
            )

    def test_rejects_fewer_than_three_values(self):
        with pytest.raises(ValueError, match="at least 3"):
            StabilitySweepDimension(
                name="x", parameter="x", values=(1.0, 2.0), baseline_index=0
            )

    def test_rejects_baseline_index_out_of_range_high(self):
        with pytest.raises(ValueError, match="baseline_index"):
            StabilitySweepDimension(
                name="x", parameter="x", values=(1.0, 2.0, 3.0), baseline_index=5
            )

    def test_rejects_baseline_index_negative(self):
        with pytest.raises(ValueError, match="baseline_index"):
            StabilitySweepDimension(
                name="x", parameter="x", values=(1.0, 2.0, 3.0), baseline_index=-1
            )

    def test_accepts_three_point_sweep(self):
        dim = StabilitySweepDimension(
            name="x", parameter="x", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        assert dim.baseline_value == 2.0
        assert dim.value_count == 3


class TestStabilitySweepPresetValidation:
    def test_rejects_empty_dimensions(self):
        with pytest.raises(ValueError, match="at least one dimension"):
            StabilitySweepPreset(strategy_key="s", dimensions=())

    def test_rejects_duplicate_dimension_names(self):
        d1 = StabilitySweepDimension(
            name="dup", parameter="a", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        d2 = StabilitySweepDimension(
            name="dup", parameter="b", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        with pytest.raises(ValueError, match="duplicate"):
            StabilitySweepPreset(strategy_key="s", dimensions=(d1, d2))

    def test_dimension_names_accessor(self):
        d1 = StabilitySweepDimension(
            name="a", parameter="a", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        d2 = StabilitySweepDimension(
            name="b", parameter="b", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        preset = StabilitySweepPreset(strategy_key="s", dimensions=(d1, d2))
        assert preset.dimension_names() == ("a", "b")


class TestComputeStabilitySummary:
    def test_baseline_metrics_and_per_point_structure(self):
        dim = _dim_pp()
        baseline = [_sold(100, 1.0, signal_time="2026-05-01"),
                    _sold(50, 0.5, signal_time="2026-05-02")]
        sweep = {
            dim.name: {
                2.0: [_sold(40, 0.4)],
                3.0: [_sold(80, 0.8)],
                4.0: [_sold(150, 1.5)],  # baseline (matches dim.baseline_index)
                5.0: [_sold(70, 0.7)],
                6.0: [_sold(30, 0.3)],
            }
        }

        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )

        assert summary["capital_base_won"] == 10_000
        assert summary["baseline"]["metrics"]["trade_count"] == 2
        dim_payload = summary["dimensions"][dim.name]
        assert dim_payload["parameter"] == "pp_ma_proximity_upper_pct"
        assert dim_payload["baseline_value"] == 4.0
        assert len(dim_payload["points"]) == 5
        # points are sorted by value (ascending)
        assert [p["value"] for p in dim_payload["points"]] == [2.0, 3.0, 4.0, 5.0, 6.0]
        # baseline point flagged
        baseline_point = next(p for p in dim_payload["points"] if p["is_baseline"])
        assert baseline_point["value"] == 4.0
        # each point has metrics + delta
        for point in dim_payload["points"]:
            assert "metrics" in point
            assert "delta" in point
        # stability block has the contract fields
        stability = dim_payload["stability"]
        assert stability["primary_metric"] == "total_net_pnl"
        assert "flag" in stability
        assert "reason" in stability

    def test_baseline_non_positive_returns_stable_with_reason(self):
        dim = _dim_pp()
        # baseline_records sum to 0 PnL
        baseline = [_sold(50, 0.5), _sold(-50, -0.5)]
        sweep = {
            dim.name: {value: [_sold(0, 0.0)] for value in dim.values}
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        stability = summary["dimensions"][dim.name]["stability"]
        assert stability["flag"] == "stable"
        assert stability["reason"] == "baseline_non_positive"

    def test_edge_baseline_returns_edge_flag(self):
        # baseline at index 0 → no left neighbor → edge
        dim = StabilitySweepDimension(
            name="x",
            parameter="x",
            values=(1.0, 2.0, 3.0),
            baseline_index=0,
        )
        sweep = {
            dim.name: {
                1.0: [_sold(100, 1.0)],
                2.0: [_sold(50, 0.5)],
                3.0: [_sold(20, 0.2)],
            }
        }
        baseline = [_sold(100, 1.0)]
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        assert summary["dimensions"]["x"]["stability"]["flag"] == "edge"

    def test_spike_classification(self):
        # baseline PnL towers above both neighbors (both drop >= 50%, ratio >= 2)
        # but neither neighbor drops 80% or more, so it's spike not cliff
        dim = _dim_pp()
        baseline = [_sold(200, 2.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(10, 0.1)],
                3.0: [_sold(80, 0.8)],   # left neighbor: 60% drop from baseline 200
                4.0: [_sold(200, 2.0)],  # baseline
                5.0: [_sold(60, 0.6)],   # right neighbor: 70% drop from baseline 200
                6.0: [_sold(10, 0.1)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        stability = summary["dimensions"][dim.name]["stability"]
        assert stability["flag"] == "spike"
        assert stability["ratio_vs_neighbors_avg"] is not None
        assert stability["ratio_vs_neighbors_avg"] >= 2.0

    def test_cliff_classification_when_one_neighbor_breaks(self):
        # baseline positive, one neighbor flips sign → cliff
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(80, 0.8)],
                3.0: [_sold(95, 0.95)],   # left neighbor: small drop
                4.0: [_sold(100, 1.0)],   # baseline
                5.0: [_sold(-30, -0.3)],  # right neighbor: sign flip → cliff
                6.0: [_sold(-50, -0.5)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        stability = summary["dimensions"][dim.name]["stability"]
        assert stability["flag"] == "cliff"

    def test_cliff_classification_when_neighbor_drops_80pct(self):
        # neighbor doesn't sign-flip but drops >= 80% → still cliff
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(50, 0.5)],
                3.0: [_sold(15, 0.15)],   # 85% drop → cliff
                4.0: [_sold(100, 1.0)],
                5.0: [_sold(90, 0.9)],
                6.0: [_sold(60, 0.6)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        assert summary["dimensions"][dim.name]["stability"]["flag"] == "cliff"

    def test_stable_classification(self):
        # all neighbors within reasonable range (< 50% drop)
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(70, 0.7)],
                3.0: [_sold(90, 0.9)],
                4.0: [_sold(100, 1.0)],
                5.0: [_sold(85, 0.85)],
                6.0: [_sold(60, 0.6)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        assert summary["dimensions"][dim.name]["stability"]["flag"] == "stable"

    def test_profit_factor_none_does_not_break_classification(self):
        # only winning trades → profit_factor is None (no losses) — must still classify
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(50, 0.5)],
                3.0: [_sold(80, 0.8)],
                4.0: [_sold(100, 1.0)],
                5.0: [_sold(80, 0.8)],
                6.0: [_sold(50, 0.5)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        # all positive, well-behaved → stable
        assert summary["dimensions"][dim.name]["stability"]["flag"] == "stable"

    def test_empty_sweep_records_for_a_value_yields_empty_metrics(self):
        # if a sweep point produced zero records, metrics should be empty (trade_count=0)
        # — and that should classify as cliff (neighbor effectively non-positive)
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(50, 0.5)],
                3.0: [],                  # left neighbor empty → 0 PnL → cliff
                4.0: [_sold(100, 1.0)],
                5.0: [_sold(80, 0.8)],
                6.0: [_sold(50, 0.5)],
            }
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        # left neighbor has 0 net_pnl (no trades) — counts as sign-flipped/0 → cliff
        assert summary["dimensions"][dim.name]["stability"]["flag"] == "cliff"

    def test_missing_dim_in_sweep_records_raises(self):
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep: dict = {}  # no records at all
        with pytest.raises(KeyError, match=dim.name):
            compute_stability_summary(
                baseline_records=baseline,
                dimensions=(dim,),
                sweep_records_by_dim=sweep,
                capital_base_won=10_000,
            )

    def test_missing_sweep_value_raises(self):
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {
                2.0: [_sold(50, 0.5)],
                3.0: [_sold(80, 0.8)],
                # 4.0 missing — baseline_value
                5.0: [_sold(80, 0.8)],
                6.0: [_sold(50, 0.5)],
            }
        }
        with pytest.raises(KeyError):
            compute_stability_summary(
                baseline_records=baseline,
                dimensions=(dim,),
                sweep_records_by_dim=sweep,
                capital_base_won=10_000,
            )

    def test_multiple_dimensions_independent(self):
        dim_a = StabilitySweepDimension(
            name="a", parameter="a", values=(1.0, 2.0, 3.0), baseline_index=1
        )
        dim_b = StabilitySweepDimension(
            name="b", parameter="b", values=(10.0, 20.0, 30.0), baseline_index=1
        )
        baseline = [_sold(100, 1.0)]
        sweep = {
            "a": {
                1.0: [_sold(80, 0.8)],
                2.0: [_sold(100, 1.0)],
                3.0: [_sold(85, 0.85)],
            },
            "b": {
                10.0: [_sold(10, 0.1)],
                20.0: [_sold(100, 1.0)],
                30.0: [_sold(15, 0.15)],
            },
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim_a, dim_b),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        assert set(summary["dimensions"].keys()) == {"a", "b"}
        # dim_a is stable
        assert summary["dimensions"]["a"]["stability"]["flag"] == "stable"
        # dim_b has 80%+ drop on both sides — that's cliff (any side >= 80% drop)
        assert summary["dimensions"]["b"]["stability"]["flag"] == "cliff"

    def test_summary_top_level_dimension_count(self):
        dim = _dim_pp()
        baseline = [_sold(100, 1.0)]
        sweep = {
            dim.name: {value: [_sold(50, 0.5)] for value in dim.values}
        }
        summary = compute_stability_summary(
            baseline_records=baseline,
            dimensions=(dim,),
            sweep_records_by_dim=sweep,
            capital_base_won=10_000,
        )
        assert summary["dimension_count"] == 1
