from __future__ import annotations

import pytest

from services.strategy_ablation_service import apply_config_overrides
from strategies.oneil_common_types import OneilPocketPivotConfig
from strategies.oneil_pocket_pivot_ablation import (
    ONEIL_POCKET_PIVOT_ABLATION_PRESET,
    PP_BGU_VARIANT_NAMES,
)


def test_preset_strategy_key_matches_run_backtest_registry():
    assert ONEIL_POCKET_PIVOT_ABLATION_PRESET.strategy_key == "oneil_pocket_pivot"


def test_preset_exposes_all_expected_variants():
    assert ONEIL_POCKET_PIVOT_ABLATION_PRESET.variant_names() == PP_BGU_VARIANT_NAMES
    assert PP_BGU_VARIANT_NAMES == (
        "disable_smart_money",
        "disable_execution_strength",
        "disable_market_timing",
        "pp_only",
        "bgu_only",
        "universe_generic_liquidity",
    )


def test_every_variant_has_a_description():
    for variant in ONEIL_POCKET_PIVOT_ABLATION_PRESET.variants:
        assert variant.description.strip(), (
            f"Variant '{variant.name}' must have a non-empty description"
        )


def _variant(name: str):
    for variant in ONEIL_POCKET_PIVOT_ABLATION_PRESET.variants:
        if variant.name == name:
            return variant
    raise KeyError(name)


def test_disable_smart_money_neutralizes_program_buy_thresholds():
    variant = _variant("disable_smart_money")
    config = apply_config_overrides(OneilPocketPivotConfig(), variant.config_overrides)

    # PP smart-money standard path: thresholds at 0% always pass when pg_buy > 0
    assert config.program_to_trade_value_pct == 0.0
    assert config.program_to_market_cap_pct == 0.0
    # BGU smart-money minimum also off
    assert config.bgu_min_pg_tv_pct == 0.0


def test_disable_execution_strength_zeroes_strength_thresholds():
    variant = _variant("disable_execution_strength")
    config = apply_config_overrides(OneilPocketPivotConfig(), variant.config_overrides)

    assert config.execution_strength_min == 0.0
    assert config.sm_flexible_execution_strength == 0.0


def test_disable_market_timing_uses_universe_override_not_config():
    variant = _variant("disable_market_timing")

    assert dict(variant.config_overrides) == {}
    assert variant.universe_overrides == {"force_market_timing_ok": True}


def test_pp_only_disables_bgu_entry():
    variant = _variant("pp_only")
    config = apply_config_overrides(OneilPocketPivotConfig(), variant.config_overrides)

    # BGU gap threshold pushed past any realistic gap so BGU never triggers
    assert config.bgu_gap_pct >= 9_999.0


def test_bgu_only_disables_pp_entry():
    variant = _variant("bgu_only")
    config = apply_config_overrides(OneilPocketPivotConfig(), variant.config_overrides)

    # PP MA upper proximity below -100% so MA proximity check never passes
    assert config.pp_ma_proximity_upper_pct <= -99.0


@pytest.mark.parametrize("variant_name", PP_BGU_VARIANT_NAMES)
def test_all_overrides_target_known_config_fields(variant_name):
    variant = _variant(variant_name)
    # Should not raise — apply_config_overrides validates field names
    apply_config_overrides(OneilPocketPivotConfig(), variant.config_overrides)
