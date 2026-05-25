from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.strategy_ablation_service import (
    AblationPreset,
    AblationVariant,
    ForceMarketTimingOkUniverseWrapper,
    apply_config_overrides,
    compute_ablation_gate_summary,
    compute_ablation_summary,
    compute_universe_exclusion_summary,
)


@dataclass
class _SampleConfig:
    threshold_a: float = 1.0
    threshold_b: int = 10


def _sold(pnl: float, ret: float, *, signal_time: str = "2026-05-01") -> dict:
    return {
        "status": "SOLD",
        "strategy": "S",
        "net_pnl": pnl,
        "net_return": ret,
        "signal_time": signal_time,
    }


def _sold_code(code: str, pnl: float, *, signal_time: str = "2026-05-01") -> dict:
    return {
        "status": "SOLD",
        "strategy": "S",
        "code": code,
        "net_pnl": pnl,
        "net_return": pnl / 1000.0,
        "signal_time": signal_time,
    }


def test_apply_config_overrides_replaces_named_fields():
    base = _SampleConfig()

    overridden = apply_config_overrides(base, {"threshold_a": 5.0})

    assert overridden.threshold_a == 5.0
    assert overridden.threshold_b == 10
    assert base.threshold_a == 1.0  # base untouched


def test_apply_config_overrides_raises_on_unknown_field():
    base = _SampleConfig()

    with pytest.raises(ValueError, match="unknown_field"):
        apply_config_overrides(base, {"unknown_field": 99})


def test_apply_config_overrides_empty_returns_equal_copy():
    base = _SampleConfig()

    result = apply_config_overrides(base, {})

    assert result.threshold_a == base.threshold_a
    assert result.threshold_b == base.threshold_b


def test_apply_config_overrides_rejects_non_mapping():
    base = _SampleConfig()

    with pytest.raises(TypeError):
        apply_config_overrides(base, [("threshold_a", 1.0)])  # type: ignore[arg-type]


def test_compute_ablation_summary_returns_baseline_and_variant_metrics():
    baseline_records = [
        _sold(100, 1.0, signal_time="2026-05-01"),
        _sold(-40, -0.5, signal_time="2026-05-02"),
    ]
    variant_records = {
        "disable_smart_money": [_sold(-50, -0.5, signal_time="2026-05-01")],
    }

    summary = compute_ablation_summary(
        baseline_records=baseline_records,
        variant_records=variant_records,
        capital_base_won=10_000,
    )

    assert summary["baseline"]["metrics"]["trade_count"] == 2
    variant = summary["variants"]["disable_smart_money"]
    assert variant["metrics"]["trade_count"] == 1
    assert variant["delta"]["trade_count_diff"] == -1
    assert variant["delta"]["total_net_pnl_diff"] == pytest.approx(-50 - 60)
    assert summary["variant_count"] == 1
    assert summary["capital_base_won"] == 10_000


def test_compute_ablation_summary_handles_empty_variant_records():
    baseline_records = [_sold(100, 1.0)]

    summary = compute_ablation_summary(
        baseline_records=baseline_records,
        variant_records={"empty_variant": []},
        capital_base_won=None,
    )

    assert summary["variants"]["empty_variant"]["metrics"]["trade_count"] == 0
    assert summary["variants"]["empty_variant"]["delta"]["trade_count_diff"] == -1


def test_compute_ablation_summary_filters_non_sold_records():
    baseline_records = [
        _sold(100, 1.0),
        {"status": "BUY", "strategy": "S"},
        {"status": "REJECTED", "strategy": "S"},
    ]

    summary = compute_ablation_summary(
        baseline_records=baseline_records,
        variant_records={},
    )

    assert summary["baseline"]["metrics"]["trade_count"] == 1
    assert summary["variant_count"] == 0


def test_compute_ablation_gate_summary_flags_variant_outperformance():
    summary = {
        "baseline": {"metrics": {"total_net_pnl": 100.0}},
        "variants": {
            "disable_filter": {
                "metrics": {"total_net_pnl": 140.0},
                "delta": {"total_net_pnl_diff": 40.0},
            },
            "strict_filter": {
                "metrics": {"total_net_pnl": 80.0},
                "delta": {"total_net_pnl_diff": -20.0},
            },
        },
    }

    gate = compute_ablation_gate_summary(
        summary,
        max_variant_outperformance_pct=20.0,
    )

    assert gate["passed"] is False
    assert gate["blocking_reasons"] == ["ablation_variant_outperforms_baseline"]
    assert gate["worst_variant"]["variant"] == "disable_filter"
    assert gate["worst_variant"]["outperformance_pct"] == 40.0


def test_compute_ablation_gate_summary_passes_when_threshold_not_exceeded():
    summary = {
        "baseline": {"metrics": {"total_net_pnl": 100.0}},
        "variants": {
            "disable_filter": {
                "metrics": {"total_net_pnl": 105.0},
                "delta": {"total_net_pnl_diff": 5.0},
            },
        },
    }

    gate = compute_ablation_gate_summary(
        summary,
        max_variant_outperformance_pct=20.0,
    )

    assert gate["passed"] is True
    assert gate["blocking_reasons"] == []


def test_compute_ablation_summary_orders_variants_deterministically():
    baseline_records = [_sold(100, 1.0), _sold(-50, -0.5)]
    summary = compute_ablation_summary(
        baseline_records=baseline_records,
        variant_records={
            "v_b": [_sold(20, 0.2)],
            "v_a": [_sold(10, 0.1)],
        },
    )

    # Insertion order preserved (dict insertion order in Python 3.7+)
    assert list(summary["variants"].keys()) == ["v_b", "v_a"]


def test_ablation_variant_is_frozen():
    variant = AblationVariant(name="v1", description="test")

    with pytest.raises(Exception):
        variant.name = "changed"  # type: ignore[misc]


def test_ablation_variant_defaults_to_empty_overrides():
    variant = AblationVariant(name="v1")

    assert dict(variant.config_overrides) == {}
    assert dict(variant.universe_overrides) == {}


def test_ablation_preset_collects_variants():
    preset = AblationPreset(
        strategy_key="oneil_pocket_pivot",
        variants=(
            AblationVariant(name="v1"),
            AblationVariant(name="v2"),
        ),
    )

    assert preset.strategy_key == "oneil_pocket_pivot"
    assert preset.variant_names() == ("v1", "v2")


# --- compute_universe_exclusion_summary (P2-2 Phase 2) -----------------------


def test_universe_exclusion_partitions_codes_into_shared_baseline_only_variant_only():
    baseline = [_sold_code("AAA", 100), _sold_code("BBB", -50)]
    variants = {
        "v": [_sold_code("AAA", 80), _sold_code("CCC", 40)],
    }

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    assert set(summary["baseline_codes"]) == {"AAA", "BBB"}
    v_report = summary["variants"]["v"]
    assert set(v_report["shared_codes"]) == {"AAA"}
    assert set(v_report["baseline_only_codes"]) == {"BBB"}
    assert set(v_report["variant_only_codes"]) == {"CCC"}


def test_universe_exclusion_aggregates_variant_only_pnl():
    baseline = [_sold_code("AAA", 100)]
    variants = {
        "v": [
            _sold_code("CCC", 40, signal_time="2026-05-01"),
            _sold_code("CCC", -10, signal_time="2026-05-03"),
            _sold_code("DDD", 70, signal_time="2026-05-02"),
        ],
    }

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    v_report = summary["variants"]["v"]
    agg = v_report["variant_only_summary"]
    assert agg["trade_count"] == 3
    assert agg["total_net_pnl"] == pytest.approx(100.0)
    assert agg["win_count"] == 2
    assert agg["loss_count"] == 1

    per_code = agg["per_code"]
    assert per_code["CCC"]["trade_count"] == 2
    assert per_code["CCC"]["total_net_pnl"] == pytest.approx(30.0)
    assert per_code["CCC"]["first_signal_time"] == "2026-05-01"
    assert per_code["CCC"]["last_signal_time"] == "2026-05-03"
    assert per_code["DDD"]["trade_count"] == 1
    assert per_code["DDD"]["total_net_pnl"] == pytest.approx(70.0)


def test_universe_exclusion_filters_non_sold_records():
    baseline = [
        _sold_code("AAA", 100),
        {"status": "REJECTED", "code": "ZZZ", "strategy": "S"},
    ]
    variants = {
        "v": [
            _sold_code("CCC", 40),
            {"status": "BUY", "code": "DDD", "strategy": "S"},
            {"status": "REJECTED", "code": "EEE", "strategy": "S"},
        ],
    }

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    assert set(summary["baseline_codes"]) == {"AAA"}
    v_report = summary["variants"]["v"]
    assert set(v_report["variant_only_codes"]) == {"CCC"}
    assert "DDD" not in v_report["variant_only_codes"]
    assert "EEE" not in v_report["variant_only_codes"]


def test_universe_exclusion_handles_empty_baseline_or_variant():
    baseline = [_sold_code("AAA", 100)]
    variants = {"empty": []}

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    empty = summary["variants"]["empty"]
    assert empty["variant_only_codes"] == []
    assert empty["shared_codes"] == []
    assert set(empty["baseline_only_codes"]) == {"AAA"}
    assert empty["variant_only_summary"]["trade_count"] == 0
    assert empty["variant_only_summary"]["total_net_pnl"] == 0.0


def test_universe_exclusion_skips_records_without_code():
    baseline = [_sold_code("AAA", 100)]
    variants = {
        "v": [
            {"status": "SOLD", "strategy": "S", "net_pnl": 50, "net_return": 0.05},  # no code
            _sold_code("CCC", 40),
        ],
    }

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    v_report = summary["variants"]["v"]
    assert set(v_report["variant_only_codes"]) == {"CCC"}


def test_universe_exclusion_codes_are_sorted_deterministically():
    baseline = [_sold_code("BBB", 0)]
    variants = {
        "v": [
            _sold_code("ZZZ", 0),
            _sold_code("AAA", 0),
            _sold_code("MMM", 0),
        ],
    }

    summary = compute_universe_exclusion_summary(
        baseline_records=baseline, variant_records=variants
    )

    v_report = summary["variants"]["v"]
    assert v_report["variant_only_codes"] == ["AAA", "MMM", "ZZZ"]


def test_ablation_preset_rejects_duplicate_variant_names():
    with pytest.raises(ValueError, match="duplicate"):
        AblationPreset(
            strategy_key="x",
            variants=(
                AblationVariant(name="v1"),
                AblationVariant(name="v1"),
            ),
        )


def test_ablation_preset_rejects_reserved_baseline_variant_name():
    with pytest.raises(ValueError, match="baseline"):
        AblationPreset(
            strategy_key="x",
            variants=(AblationVariant(name="baseline"),),
        )


def test_ablation_preset_rejects_empty_variants():
    with pytest.raises(ValueError, match="at least one"):
        AblationPreset(strategy_key="x", variants=())


@pytest.mark.asyncio
async def test_force_market_timing_wrapper_returns_true_and_delegates_others():
    class _StubUniverse:
        def __init__(self) -> None:
            self.timing_calls = 0
            self.watchlist_calls = 0

        async def is_market_timing_ok(self, market, caller="", logger=None):
            self.timing_calls += 1
            return False  # baseline would block

        async def get_watchlist(self, *args, **kwargs):
            self.watchlist_calls += 1
            return ["005930"]

        @property
        def some_attr(self) -> str:
            return "attr_value"

    inner = _StubUniverse()
    wrapper = ForceMarketTimingOkUniverseWrapper(inner)

    assert await wrapper.is_market_timing_ok("KOSPI") is True
    assert await wrapper.is_market_timing_ok("KOSDAQ", caller="x", logger=None) is True
    # Wrapper short-circuits — inner timing not called
    assert inner.timing_calls == 0

    # Other methods delegated
    assert await wrapper.get_watchlist() == ["005930"]
    assert inner.watchlist_calls == 1
    assert wrapper.some_attr == "attr_value"
