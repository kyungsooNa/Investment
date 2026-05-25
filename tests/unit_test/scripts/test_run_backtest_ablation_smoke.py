from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.run_backtest import (
    ACTIVE_BACKTEST_STRATEGIES,
    _build_ablation_overrides,
    _build_default_strategy_config,
    _filter_ablation_variants,
    _format_ablation_console_lines,
    _format_universe_exclusion_console_lines,
    _parse_args,
    _resolve_ablation_preset,
    _run_ablation_for_result,
)
from services.backtest_period_runner import BacktestPeriodRunResult
from services.strategy_ablation_service import AblationPreset, AblationVariant
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
    with pytest.raises(ValueError, match="not_a_real_strategy"):
        _resolve_ablation_preset("not_a_real_strategy")


@pytest.mark.parametrize("strategy_key", list(ACTIVE_BACKTEST_STRATEGIES))
def test_resolve_ablation_preset_covers_every_active_strategy(strategy_key):
    preset = _resolve_ablation_preset(strategy_key)

    assert isinstance(preset, AblationPreset)
    assert preset.strategy_key == strategy_key
    assert len(preset.variants) >= 3


@pytest.mark.parametrize("strategy_key", list(ACTIVE_BACKTEST_STRATEGIES))
def test_build_default_strategy_config_returns_dataclass_instance(strategy_key):
    import dataclasses

    config = _build_default_strategy_config(strategy_key)

    assert dataclasses.is_dataclass(config), (
        f"_build_default_strategy_config('{strategy_key}') must return a dataclass instance"
    )


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
    assert payload["gate"]["passed"] is True


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


def test_build_ablation_overrides_passthrough_when_variant_none():
    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)

    universe, config = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=None,
    )

    assert universe is base
    assert config is None


def test_build_ablation_overrides_generic_liquidity_swaps_universe():
    from services.generic_liquidity_universe_service import (
        GenericLiquidityUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=object())
    variant = AblationVariant(
        name="universe_generic_liquidity",
        universe_overrides={"universe_type": "generic_liquidity"},
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, GenericLiquidityUniverseService)
    assert universe._sqs is base._sqs
    assert universe._tm is base._tm
    assert universe._regime_svc is base._regime_svc


def test_build_ablation_overrides_generic_liquidity_passes_threshold_overrides():
    from services.generic_liquidity_universe_service import (
        GenericLiquidityUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)
    variant = AblationVariant(
        name="universe_generic_relaxed",
        universe_overrides={
            "universe_type": "generic_liquidity",
            "min_avg_trading_value_5d": 1_000_000_000,
            "min_market_cap": 50_000_000_000,
            "max_watchlist": 50,
        },
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, GenericLiquidityUniverseService)
    assert universe._min_tv_5d == 1_000_000_000
    assert universe._min_cap == 50_000_000_000
    assert universe._max_watchlist == 50


def test_build_ablation_overrides_combines_generic_liquidity_with_force_market_timing():
    from services.generic_liquidity_universe_service import (
        GenericLiquidityUniverseService,
    )
    from services.strategy_ablation_service import (
        ForceMarketTimingOkUniverseWrapper,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)
    variant = AblationVariant(
        name="universe_generic_force_mt",
        universe_overrides={
            "universe_type": "generic_liquidity",
            "force_market_timing_ok": True,
        },
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    # 외부는 force-market-timing 래퍼, 내부는 generic liquidity universe 여야 한다.
    assert isinstance(universe, ForceMarketTimingOkUniverseWrapper)
    assert isinstance(universe._inner, GenericLiquidityUniverseService)


def test_vbo_ablation_preset_contains_universe_generic_liquidity_variant():
    from strategies.larry_williams_vbo_ablation import (
        LARRY_WILLIAMS_VBO_ABLATION_PRESET,
        VBO_VARIANT_NAMES,
    )

    assert "universe_generic_liquidity" in VBO_VARIANT_NAMES
    by_name = {v.name: v for v in LARRY_WILLIAMS_VBO_ABLATION_PRESET.variants}
    variant = by_name.get("universe_generic_liquidity")
    assert variant is not None
    assert variant.universe_overrides.get("universe_type") == "generic_liquidity"


def test_build_ablation_overrides_rsi2_mean_reversion_swaps_universe():
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=object())
    variant = AblationVariant(
        name="universe_rsi2_mean_reversion",
        universe_overrides={"universe_type": "rsi2_mean_reversion"},
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="rsi2_pullback",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, Rsi2MeanReversionUniverseService)
    assert universe._sqs is base._sqs
    assert universe._tm is base._tm
    assert universe._regime_svc is base._regime_svc
    # 기본 변동성 floor 가 적용된다.
    assert universe.min_volatility_20d_annualized == pytest.approx(0.30)


def test_build_ablation_overrides_rsi2_mean_reversion_passes_volatility_override():
    from services.rsi2_mean_reversion_universe_service import (
        Rsi2MeanReversionUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)
    variant = AblationVariant(
        name="universe_rsi2_mean_reversion_strict",
        universe_overrides={
            "universe_type": "rsi2_mean_reversion",
            "min_volatility_20d_annualized": 0.45,
            "min_avg_trading_value_5d": 3_000_000_000,
        },
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="rsi2_pullback",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, Rsi2MeanReversionUniverseService)
    assert universe.min_volatility_20d_annualized == pytest.approx(0.45)
    assert universe._min_tv_5d == 3_000_000_000


def test_build_ablation_overrides_vbo_volatility_swaps_universe():
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=object())
    variant = AblationVariant(
        name="universe_vbo_volatility",
        universe_overrides={"universe_type": "vbo_volatility"},
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, VboVolatilityUniverseService)
    assert universe._sqs is base._sqs
    assert universe._tm is base._tm
    assert universe._regime_svc is base._regime_svc
    assert universe.min_volatility_20d_annualized == pytest.approx(0.35)


def test_build_ablation_overrides_vbo_volatility_passes_volatility_override():
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)
    variant = AblationVariant(
        name="universe_vbo_volatility_strict",
        universe_overrides={
            "universe_type": "vbo_volatility",
            "min_volatility_20d_annualized": 0.50,
            "min_market_cap": 50_000_000_000,
            "max_watchlist": 40,
        },
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, VboVolatilityUniverseService)
    assert universe.min_volatility_20d_annualized == pytest.approx(0.50)
    assert universe._min_cap == 50_000_000_000
    assert universe._max_watchlist == 40


def test_build_ablation_overrides_combines_vbo_volatility_with_force_market_timing():
    from services.vbo_volatility_universe_service import (
        VboVolatilityUniverseService,
    )
    from services.strategy_ablation_service import (
        ForceMarketTimingOkUniverseWrapper,
    )

    base = SimpleNamespace(_sqs=object(), _tm=object(), _regime_svc=None)
    variant = AblationVariant(
        name="universe_vbo_volatility_force_mt",
        universe_overrides={
            "universe_type": "vbo_volatility",
            "force_market_timing_ok": True,
        },
    )

    universe, _ = _build_ablation_overrides(
        strategy_key="larry_williams_vbo",
        base_universe=base,
        variant=variant,
    )

    assert isinstance(universe, ForceMarketTimingOkUniverseWrapper)
    assert isinstance(universe._inner, VboVolatilityUniverseService)


def test_rsi2_ablation_preset_contains_universe_rsi2_mean_reversion_variant():
    from strategies.rsi2_pullback_ablation import (
        RSI2_PULLBACK_ABLATION_PRESET,
        RSI2_VARIANT_NAMES,
    )

    assert "universe_rsi2_mean_reversion" in RSI2_VARIANT_NAMES
    by_name = {v.name: v for v in RSI2_PULLBACK_ABLATION_PRESET.variants}
    variant = by_name.get("universe_rsi2_mean_reversion")
    assert variant is not None
    assert variant.universe_overrides.get("universe_type") == "rsi2_mean_reversion"


def test_vbo_ablation_preset_contains_universe_vbo_volatility_variant():
    from strategies.larry_williams_vbo_ablation import (
        LARRY_WILLIAMS_VBO_ABLATION_PRESET,
        VBO_VARIANT_NAMES,
    )

    assert "universe_vbo_volatility" in VBO_VARIANT_NAMES
    by_name = {v.name: v for v in LARRY_WILLIAMS_VBO_ABLATION_PRESET.variants}
    variant = by_name.get("universe_vbo_volatility")
    assert variant is not None
    assert variant.universe_overrides.get("universe_type") == "vbo_volatility"


@pytest.mark.parametrize("strategy_key", list(ACTIVE_BACKTEST_STRATEGIES))
def test_every_active_strategy_ablation_preset_has_universe_generic_liquidity_variant(
    strategy_key,
):
    """Phase 1: 7개 활성 전략 모두에 universe_generic_liquidity variant 가 정의되어 있어야 한다."""
    preset = _resolve_ablation_preset(strategy_key)
    by_name = {v.name: v for v in preset.variants}
    variant = by_name.get("universe_generic_liquidity")
    assert variant is not None, (
        f"strategy '{strategy_key}' 의 ablation preset 에 "
        f"'universe_generic_liquidity' variant 가 없습니다."
    )
    assert variant.universe_overrides.get("universe_type") == "generic_liquidity"


# --- Phase 2: universe exclusion summary wiring & console formatter ---------


@pytest.mark.asyncio
async def test_run_ablation_for_result_attaches_universe_exclusion_summary():
    """Phase 2: _run_ablation_for_result 가 universe_exclusion 키를 같이 첨부한다."""
    baseline = BacktestPeriodRunResult(
        strategy_name="VBO",
        dates=["20260501"],
        journal_records=[
            {
                "status": "SOLD",
                "strategy": "S",
                "code": "AAA",
                "net_pnl": 100,
                "net_return": 0.1,
                "signal_time": "2026-05-01",
            },
        ],
    )

    async def fake_run_variant(variant: AblationVariant) -> BacktestPeriodRunResult:
        return BacktestPeriodRunResult(
            strategy_name="VBO",
            dates=["20260501"],
            journal_records=[
                {
                    "status": "SOLD",
                    "strategy": "S",
                    "code": "CCC",
                    "net_pnl": 70,
                    "net_return": 0.07,
                    "signal_time": "2026-05-01",
                },
            ],
        )

    args = SimpleNamespace(
        ablation="larry_williams_vbo",
        ablation_variants="universe_generic_liquidity",
        initial_cash=1_000_000.0,
    )

    await _run_ablation_for_result(baseline, args, run_variant_fn=fake_run_variant)

    payload = getattr(baseline, "ablation")
    assert "universe_exclusion" in payload
    exclusion = payload["universe_exclusion"]
    assert exclusion["baseline_codes"] == ["AAA"]
    v_report = exclusion["variants"]["universe_generic_liquidity"]
    assert v_report["variant_only_codes"] == ["CCC"]
    assert v_report["baseline_only_codes"] == ["AAA"]
    assert v_report["variant_only_summary"]["trade_count"] == 1
    assert v_report["variant_only_summary"]["total_net_pnl"] == pytest.approx(70.0)


def test_format_universe_exclusion_console_lines_produces_header_and_rows():
    exclusion = {
        "baseline_codes": ["AAA", "BBB"],
        "variants": {
            "universe_generic_liquidity": {
                "variant_only_codes": ["CCC", "DDD"],
                "baseline_only_codes": ["BBB"],
                "shared_codes": ["AAA"],
                "variant_only_summary": {
                    "trade_count": 3,
                    "total_net_pnl": 120.0,
                    "win_count": 2,
                    "loss_count": 1,
                    "per_code": {
                        "CCC": {
                            "trade_count": 2,
                            "total_net_pnl": 80.0,
                            "first_signal_time": "2026-05-01",
                            "last_signal_time": "2026-05-03",
                        },
                        "DDD": {
                            "trade_count": 1,
                            "total_net_pnl": 40.0,
                            "first_signal_time": "2026-05-02",
                            "last_signal_time": "2026-05-02",
                        },
                    },
                },
            }
        },
    }

    lines = _format_universe_exclusion_console_lines("larry_williams_vbo", exclusion)

    rendered = "\n".join(lines)
    assert "UNIVERSE_EXCLUSION" in rendered
    assert "larry_williams_vbo" in rendered
    assert "universe_generic_liquidity" in rendered
    assert "variant_only=  2" in rendered or "variant_only=2" in rendered
    # Aggregate net pnl 가 어딘가 표시되어야 한다 (천 단위 콤마 또는 단순 숫자)
    assert any("120" in line for line in lines)


def test_format_universe_exclusion_console_lines_returns_empty_when_no_variants():
    """variants 가 비어있으면 헤더도 출력하지 않는다."""
    lines = _format_universe_exclusion_console_lines(
        "larry_williams_vbo", {"baseline_codes": [], "variants": {}}
    )
    assert lines == []
