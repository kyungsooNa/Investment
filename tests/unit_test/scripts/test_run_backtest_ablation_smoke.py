from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_backtest import (
    _filter_ablation_variants,
    _format_ablation_console_lines,
    _parse_args,
    _resolve_ablation_preset,
    _run_ablation_for_result,
)
from services.backtest_period_runner import BacktestPeriodRunResult
from services.strategy_ablation_service import AblationVariant
from strategies.oneil_pocket_pivot_ablation import (
    ONEIL_POCKET_PIVOT_ABLATION_PRESET,
)


def test_parse_args_accepts_ablation_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest",
            "--dates",
            "20260501",
            "--ablation",
            "oneil_pocket_pivot",
            "--ablation-variants",
            "disable_smart_money,pp_only",
        ],
    )

    args = _parse_args()

    assert args.ablation == "oneil_pocket_pivot"
    assert args.ablation_variants == "disable_smart_money,pp_only"


def test_parse_args_ablation_defaults_to_none(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_backtest", "--dates", "20260501"])

    args = _parse_args()

    assert args.ablation is None
    assert args.ablation_variants is None


def test_resolve_ablation_preset_returns_known_preset():
    preset = _resolve_ablation_preset("oneil_pocket_pivot")

    assert preset is ONEIL_POCKET_PIVOT_ABLATION_PRESET


def test_resolve_ablation_preset_raises_on_unknown_strategy():
    with pytest.raises(ValueError, match="oneil_squeeze_breakout"):
        _resolve_ablation_preset("oneil_squeeze_breakout")


def test_filter_ablation_variants_returns_all_when_none():
    selected = _filter_ablation_variants(ONEIL_POCKET_PIVOT_ABLATION_PRESET, None)

    assert selected == ONEIL_POCKET_PIVOT_ABLATION_PRESET.variants


def test_filter_ablation_variants_returns_subset_when_named():
    selected = _filter_ablation_variants(
        ONEIL_POCKET_PIVOT_ABLATION_PRESET, "pp_only,bgu_only"
    )

    names = tuple(v.name for v in selected)
    assert names == ("pp_only", "bgu_only")


def test_filter_ablation_variants_raises_on_unknown_variant():
    with pytest.raises(ValueError, match="not_a_real_variant"):
        _filter_ablation_variants(
            ONEIL_POCKET_PIVOT_ABLATION_PRESET, "pp_only,not_a_real_variant"
        )


@pytest.mark.asyncio
async def test_run_ablation_for_result_runs_each_variant_and_attaches_summary():
    baseline = BacktestPeriodRunResult(
        strategy_name="오닐PP/BGU",
        dates=["20260501"],
        journal_records=[
            {"status": "SOLD", "strategy": "S", "net_pnl": 100, "net_return": 1.0},
            {"status": "SOLD", "strategy": "S", "net_pnl": -50, "net_return": -0.5},
        ],
    )

    captured_variants: list[str] = []

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        captured_variants.append(variant.name)
        return BacktestPeriodRunResult(
            strategy_name="오닐PP/BGU",
            dates=["20260501"],
            journal_records=[
                {
                    "status": "SOLD",
                    "strategy": "S",
                    "net_pnl": 10,
                    "net_return": 0.1,
                },
            ],
        )

    args = SimpleNamespace(
        ablation="oneil_pocket_pivot",
        ablation_variants="pp_only,bgu_only",
        initial_cash=1_000_000.0,
    )

    await _run_ablation_for_result(baseline, args, run_variant_fn=fake_run_variant)

    assert captured_variants == ["pp_only", "bgu_only"]
    payload = getattr(baseline, "ablation")
    assert payload["strategy_key"] == "oneil_pocket_pivot"
    summary = payload["summary"]
    assert set(summary["variants"].keys()) == {"pp_only", "bgu_only"}
    assert summary["variants"]["pp_only"]["metrics"]["trade_count"] == 1
    assert summary["baseline"]["metrics"]["trade_count"] == 2


@pytest.mark.asyncio
async def test_run_ablation_for_result_skips_when_ablation_arg_missing():
    baseline = BacktestPeriodRunResult(
        strategy_name="S", dates=["20260501"], journal_records=[]
    )
    args = SimpleNamespace(
        ablation=None, ablation_variants=None, initial_cash=1.0
    )
    runner = AsyncMock()

    await _run_ablation_for_result(baseline, args, run_variant_fn=runner)

    runner.assert_not_awaited()
    assert not hasattr(baseline, "ablation") or baseline.ablation is None  # type: ignore[attr-defined]


def test_format_ablation_console_lines_produces_header_and_rows():
    summary = {
        "capital_base_won": 1_000_000,
        "baseline": {
            "metrics": {
                "trade_count": 2,
                "win_rate": 0.5,
                "avg_net_return": 0.25,
                "total_net_pnl": 50.0,
                "profit_factor": 2.0,
                "payoff_ratio": 2.0,
                "mdd_amount": 50.0,
            }
        },
        "variants": {
            "pp_only": {
                "metrics": {
                    "trade_count": 1,
                    "win_rate": 1.0,
                    "avg_net_return": 0.5,
                    "total_net_pnl": 50.0,
                    "profit_factor": None,
                    "payoff_ratio": None,
                    "mdd_amount": 0.0,
                },
                "delta": {
                    "trade_count_diff": -1,
                    "win_rate_diff": 0.5,
                    "avg_net_return_diff": 0.25,
                    "total_net_pnl_diff": 0.0,
                    "profit_factor_diff": None,
                    "payoff_ratio_diff": None,
                    "mdd_amount_diff": -50.0,
                },
            }
        },
        "variant_count": 1,
    }

    lines = _format_ablation_console_lines("oneil_pocket_pivot", summary)

    rendered = "\n".join(lines)
    assert "oneil_pocket_pivot" in rendered
    assert "baseline" in rendered.lower()
    assert "pp_only" in rendered
    # Variant metrics should appear
    assert any("trade" in line.lower() for line in lines)
