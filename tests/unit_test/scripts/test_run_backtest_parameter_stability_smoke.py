from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_backtest import (
    ACTIVE_BACKTEST_STRATEGIES,
    _build_default_strategy_config,
    _filter_parameter_stability_dimensions,
    _format_parameter_stability_console_lines,
    _parse_args,
    _resolve_parameter_stability_preset,
    _run_parameter_stability_for_result,
)
from services.backtest_period_runner import BacktestPeriodRunResult
from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
    compute_stability_summary,
)
from services.strategy_ablation_service import AblationVariant, apply_config_overrides
from strategies.oneil_pocket_pivot_parameter_stability import (
    ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET,
)


def test_parse_args_accepts_parameter_stability_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--parameter-stability",
            "oneil_pocket_pivot",
            "--parameter-stability-dimensions",
            "pp_ma_proximity_upper_pct,bgu_gap_pct",
        ],
    )

    args = _parse_args()

    assert args.parameter_stability == "oneil_pocket_pivot"
    assert args.parameter_stability_dimensions == "pp_ma_proximity_upper_pct,bgu_gap_pct"


def test_parse_args_parameter_stability_defaults_to_none(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_backtest", "--dates", "20260501"])

    args = _parse_args()

    assert args.parameter_stability is None
    assert args.parameter_stability_dimensions is None


def test_resolve_parameter_stability_preset_returns_known_preset():
    preset = _resolve_parameter_stability_preset("oneil_pocket_pivot")

    assert preset is ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET


def test_resolve_parameter_stability_preset_raises_on_unknown_strategy():
    with pytest.raises(ValueError, match="not_a_real_strategy"):
        _resolve_parameter_stability_preset("not_a_real_strategy")


@pytest.mark.parametrize("strategy_key", list(ACTIVE_BACKTEST_STRATEGIES))
def test_resolve_parameter_stability_preset_covers_every_active_strategy(strategy_key):
    preset = _resolve_parameter_stability_preset(strategy_key)

    assert isinstance(preset, StabilitySweepPreset)
    assert preset.strategy_key == strategy_key
    assert len(preset.dimensions) >= 1


@pytest.mark.parametrize("strategy_key", list(ACTIVE_BACKTEST_STRATEGIES))
def test_parameter_stability_preset_overrides_apply_to_default_config(strategy_key):
    """Each dimension's parameter must be a real field on the strategy config dataclass.

    apply_config_overrides raises ValueError on unknown fields, so this test
    fails loudly if a preset references a field that no longer exists.
    """
    config = _build_default_strategy_config(strategy_key)
    assert dataclasses.is_dataclass(config)

    preset = _resolve_parameter_stability_preset(strategy_key)
    for dim in preset.dimensions:
        for value in dim.values:
            overridden = apply_config_overrides(config, {dim.parameter: value})
            assert getattr(overridden, dim.parameter) == value


def test_filter_parameter_stability_dimensions_returns_all_when_none():
    selected = _filter_parameter_stability_dimensions(
        ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET, None
    )

    assert selected == ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET.dimensions


def test_filter_parameter_stability_dimensions_returns_subset_when_named():
    selected = _filter_parameter_stability_dimensions(
        ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET,
        "pp_ma_proximity_upper_pct,bgu_gap_pct",
    )

    names = tuple(d.name for d in selected)
    assert names == ("pp_ma_proximity_upper_pct", "bgu_gap_pct")


def test_filter_parameter_stability_dimensions_raises_on_unknown_dimension():
    with pytest.raises(ValueError, match="not_a_real_dimension"):
        _filter_parameter_stability_dimensions(
            ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET,
            "pp_ma_proximity_upper_pct,not_a_real_dimension",
        )


@pytest.mark.asyncio
async def test_run_parameter_stability_for_result_runs_each_point_and_attaches_summary():
    baseline = BacktestPeriodRunResult(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        journal_records=[
            {"status": "SOLD", "strategy": "S", "net_pnl": 100, "net_return": 1.0},
            {"status": "SOLD", "strategy": "S", "net_pnl": -50, "net_return": -0.5},
        ],
    )

    captured: list[tuple[str, object]] = []

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        # The synthesized variant's config_overrides must contain exactly one entry
        # — the dimension parameter mapped to the sweep value.
        assert len(variant.config_overrides) == 1
        param, value = next(iter(variant.config_overrides.items()))
        captured.append((param, value))
        return BacktestPeriodRunResult(
            strategy_name="오닐PP/BGU",
            dates=["20260501"],
            journal_records=[
                {"status": "SOLD", "strategy": "S", "net_pnl": 20, "net_return": 0.2}
            ],
        )

    args = SimpleNamespace(
        parameter_stability="oneil_pocket_pivot",
        parameter_stability_dimensions="pp_ma_proximity_upper_pct",
        initial_cash=1_000_000.0,
    )

    await _run_parameter_stability_for_result(
        baseline, args, run_variant_fn=fake_run_variant
    )

    # 5 sweep points captured for the single chosen dimension
    assert len(captured) == 5
    assert {p for p, _ in captured} == {"pp_ma_proximity_upper_pct"}

    payload = getattr(baseline, "parameter_stability")
    assert payload["strategy_key"] == "oneil_pocket_pivot"
    summary = payload["summary"]
    assert summary["dimension_count"] == 1
    assert "pp_ma_proximity_upper_pct" in summary["dimensions"]
    points = summary["dimensions"]["pp_ma_proximity_upper_pct"]["points"]
    assert len(points) == 5
    # baseline metrics are attached at the top
    assert summary["baseline"]["metrics"]["trade_count"] == 2


@pytest.mark.asyncio
async def test_run_parameter_stability_for_result_runs_all_dimensions_by_default():
    baseline = BacktestPeriodRunResult(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        journal_records=[
            {"status": "SOLD", "strategy": "S", "net_pnl": 50, "net_return": 0.5}
        ],
    )

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        return BacktestPeriodRunResult(
            strategy_name="오닐PP/BGU",
            dates=["20260501"],
            journal_records=[
                {"status": "SOLD", "strategy": "S", "net_pnl": 10, "net_return": 0.1}
            ],
        )

    runner = AsyncMock(side_effect=fake_run_variant)

    args = SimpleNamespace(
        parameter_stability="oneil_pocket_pivot",
        parameter_stability_dimensions=None,
        initial_cash=1_000_000.0,
    )

    await _run_parameter_stability_for_result(
        baseline, args, run_variant_fn=runner
    )

    # 3 dimensions × 5 sweep points = 15 invocations
    assert runner.await_count == 15
    summary = baseline.parameter_stability["summary"]  # type: ignore[attr-defined]
    assert summary["dimension_count"] == 3


@pytest.mark.asyncio
async def test_run_parameter_stability_for_result_skips_when_arg_missing():
    baseline = BacktestPeriodRunResult(
        strategy_name="S", dates=["20260501"], journal_records=[]
    )
    args = SimpleNamespace(
        parameter_stability=None,
        parameter_stability_dimensions=None,
        initial_cash=1.0,
    )
    runner = AsyncMock()

    await _run_parameter_stability_for_result(
        baseline, args, run_variant_fn=runner
    )

    runner.assert_not_awaited()
    assert not hasattr(baseline, "parameter_stability") or baseline.parameter_stability is None  # type: ignore[attr-defined]


def test_format_parameter_stability_console_lines_produces_dimension_table():
    dim = StabilitySweepDimension(
        name="pp_ma_proximity_upper_pct",
        parameter="pp_ma_proximity_upper_pct",
        values=(2.0, 3.0, 4.0, 5.0, 6.0),
        baseline_index=2,
    )
    baseline_records = [{"status": "SOLD", "strategy": "S", "net_pnl": 100, "net_return": 1.0}]
    sweep = {
        dim.name: {
            2.0: [{"status": "SOLD", "strategy": "S", "net_pnl": 30, "net_return": 0.3}],
            3.0: [{"status": "SOLD", "strategy": "S", "net_pnl": 50, "net_return": 0.5}],
            4.0: [{"status": "SOLD", "strategy": "S", "net_pnl": 100, "net_return": 1.0}],
            5.0: [{"status": "SOLD", "strategy": "S", "net_pnl": 60, "net_return": 0.6}],
            6.0: [{"status": "SOLD", "strategy": "S", "net_pnl": 40, "net_return": 0.4}],
        }
    }
    summary = compute_stability_summary(
        baseline_records=baseline_records,
        dimensions=(dim,),
        sweep_records_by_dim=sweep,
        capital_base_won=1_000_000.0,
    )

    lines = _format_parameter_stability_console_lines(
        "oneil_pocket_pivot", summary
    )

    rendered = "\n".join(lines)
    assert "oneil_pocket_pivot" in rendered
    assert "pp_ma_proximity_upper_pct" in rendered
    assert "baseline" in rendered.lower()
    # The five sweep values must all appear in the rendered table.
    for value in ("2.0", "3.0", "4.0", "5.0", "6.0"):
        assert value in rendered
    # stability flag line surfaced
    assert "stability=" in rendered
