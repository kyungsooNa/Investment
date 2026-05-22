"""Ablation preset coverage for the remaining 6 active strategies.

The O'Neil Pocket Pivot / BGU preset has its own dedicated file at
``test_oneil_pocket_pivot_ablation.py`` because it predates this batch. The
tests here apply identical structural checks across the rest of the active
strategy registry.
"""
from __future__ import annotations

import pytest

from services.strategy_ablation_service import (
    AblationPreset,
    apply_config_overrides,
)
from strategies.first_pullback_ablation import (
    FIRST_PULLBACK_ABLATION_PRESET,
    FIRST_PULLBACK_VARIANT_NAMES,
)
from strategies.first_pullback_types import FirstPullbackConfig
from strategies.high_tight_flag_ablation import (
    HIGH_TIGHT_FLAG_ABLATION_PRESET,
    HTF_VARIANT_NAMES,
)
from strategies.larry_williams_cb_types import LarryWilliamsCBConfig
from strategies.larry_williams_channel_breakout_ablation import (
    CB_VARIANT_NAMES,
    LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET,
)
from strategies.larry_williams_vbo_ablation import (
    LARRY_WILLIAMS_VBO_ABLATION_PRESET,
    VBO_VARIANT_NAMES,
)
from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOConfig
from strategies.oneil_common_types import HTFConfig, OneilBreakoutConfig
from strategies.oneil_squeeze_breakout_ablation import (
    ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET,
    OSB_VARIANT_NAMES,
)
from strategies.rsi2_pullback_ablation import (
    RSI2_PULLBACK_ABLATION_PRESET,
    RSI2_VARIANT_NAMES,
)
from strategies.rsi2_pullback_types import RSI2PullbackConfig


# (strategy_key, preset, variant_names_tuple, default_config_factory)
_PRESETS = [
    (
        "oneil_squeeze_breakout",
        ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET,
        OSB_VARIANT_NAMES,
        OneilBreakoutConfig,
    ),
    (
        "high_tight_flag",
        HIGH_TIGHT_FLAG_ABLATION_PRESET,
        HTF_VARIANT_NAMES,
        HTFConfig,
    ),
    (
        "first_pullback",
        FIRST_PULLBACK_ABLATION_PRESET,
        FIRST_PULLBACK_VARIANT_NAMES,
        FirstPullbackConfig,
    ),
    (
        "larry_williams_vbo",
        LARRY_WILLIAMS_VBO_ABLATION_PRESET,
        VBO_VARIANT_NAMES,
        LarryWilliamsVBOConfig,
    ),
    (
        "rsi2_pullback",
        RSI2_PULLBACK_ABLATION_PRESET,
        RSI2_VARIANT_NAMES,
        RSI2PullbackConfig,
    ),
    (
        "larry_williams_channel_breakout",
        LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET,
        CB_VARIANT_NAMES,
        LarryWilliamsCBConfig,
    ),
]


@pytest.mark.parametrize(
    "strategy_key,preset,expected_names,_factory",
    _PRESETS,
    ids=[entry[0] for entry in _PRESETS],
)
def test_preset_metadata_matches_strategy_key(
    strategy_key, preset: AblationPreset, expected_names, _factory
):
    assert preset.strategy_key == strategy_key
    assert preset.variant_names() == expected_names


@pytest.mark.parametrize(
    "_strategy_key,preset,_names,_factory",
    _PRESETS,
    ids=[entry[0] for entry in _PRESETS],
)
def test_every_variant_has_a_non_empty_description(
    _strategy_key, preset: AblationPreset, _names, _factory
):
    for variant in preset.variants:
        assert variant.description.strip(), (
            f"Variant '{variant.name}' in '{preset.strategy_key}' "
            "must have a non-empty description"
        )


@pytest.mark.parametrize(
    "_strategy_key,preset,_names,config_factory",
    _PRESETS,
    ids=[entry[0] for entry in _PRESETS],
)
def test_every_variant_config_overrides_target_known_fields(
    _strategy_key, preset: AblationPreset, _names, config_factory
):
    for variant in preset.variants:
        # Should not raise — apply_config_overrides validates field names
        apply_config_overrides(config_factory(), variant.config_overrides)


@pytest.mark.parametrize(
    "_strategy_key,preset,_names,_factory",
    _PRESETS,
    ids=[entry[0] for entry in _PRESETS],
)
def test_market_timing_variant_uses_universe_override_not_config(
    _strategy_key, preset: AblationPreset, _names, _factory
):
    timing_variants = [
        v for v in preset.variants if v.name == "disable_market_timing"
    ]
    assert timing_variants, (
        f"Every active-strategy preset should expose a 'disable_market_timing' "
        f"variant; preset '{preset.strategy_key}' has none."
    )
    variant = timing_variants[0]
    assert dict(variant.config_overrides) == {}
    assert variant.universe_overrides == {"force_market_timing_ok": True}


def test_osb_disable_smart_money_zeroes_program_thresholds():
    variant = next(
        v for v in ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET.variants
        if v.name == "disable_smart_money"
    )
    config = apply_config_overrides(OneilBreakoutConfig(), variant.config_overrides)

    assert config.program_to_trade_value_pct == 0.0
    assert config.program_to_market_cap_pct == 0.0


def test_htf_relax_pattern_check_neutralizes_pole_and_flag_gates():
    variant = next(
        v for v in HIGH_TIGHT_FLAG_ABLATION_PRESET.variants
        if v.name == "relax_pattern_check"
    )
    config = apply_config_overrides(HTFConfig(), variant.config_overrides)

    assert config.pole_min_surge_ratio == 0.0
    assert config.flag_max_drawdown_pct >= 999.0


def test_first_pullback_widen_range_accepts_full_pullback_band():
    variant = next(
        v for v in FIRST_PULLBACK_ABLATION_PRESET.variants
        if v.name == "widen_pullback_range"
    )
    config = apply_config_overrides(FirstPullbackConfig(), variant.config_overrides)

    assert config.pullback_lower_pct <= -99.0
    assert config.pullback_upper_pct >= 99.0


def test_vbo_disable_program_buy_zeroes_ratio():
    variant = next(
        v for v in LARRY_WILLIAMS_VBO_ABLATION_PRESET.variants
        if v.name == "disable_program_buy"
    )
    config = apply_config_overrides(LarryWilliamsVBOConfig(), variant.config_overrides)

    assert config.program_buy_ratio == 0.0


def test_rsi2_disable_rsi_oversold_raises_threshold_to_100():
    variant = next(
        v for v in RSI2_PULLBACK_ABLATION_PRESET.variants
        if v.name == "disable_rsi_oversold"
    )
    config = apply_config_overrides(RSI2PullbackConfig(), variant.config_overrides)

    assert config.rsi_threshold >= 100.0


def test_rsi2_disable_minervini_flips_boolean_to_false():
    variant = next(
        v for v in RSI2_PULLBACK_ABLATION_PRESET.variants
        if v.name == "disable_minervini_stage2"
    )
    config = apply_config_overrides(RSI2PullbackConfig(), variant.config_overrides)

    assert config.require_minervini_stage2 is False


def test_channel_breakout_disable_adx_zeroes_threshold():
    variant = next(
        v for v in LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET.variants
        if v.name == "disable_adx_filter"
    )
    config = apply_config_overrides(LarryWilliamsCBConfig(), variant.config_overrides)

    assert config.adx_threshold == 0.0
