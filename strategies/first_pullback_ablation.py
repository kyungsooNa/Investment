"""Ablation variants for the First Pullback strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


FIRST_PULLBACK_VARIANT_NAMES: tuple[str, ...] = (
    "disable_execution_strength",
    "disable_market_timing",
    "disable_rapid_surge",
    "disable_ma_rising",
    "widen_pullback_range",
)


FIRST_PULLBACK_ABLATION_PRESET = AblationPreset(
    strategy_key="first_pullback",
    variants=(
        AblationVariant(
            name="disable_execution_strength",
            description="Zero execution-strength so cgld_val checks never reject.",
            config_overrides={"execution_strength_min": 0.0},
        ),
        AblationVariant(
            name="disable_market_timing",
            description=(
                "Force is_market_timing_ok to True via a universe-service wrapper."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
        AblationVariant(
            name="disable_rapid_surge",
            description=(
                "Zero rapid-surge percent so any prior price change qualifies the "
                "candidate as a surge."
            ),
            config_overrides={"rapid_surge_pct": 0.0},
        ),
        AblationVariant(
            name="disable_ma_rising",
            description=(
                "Set ma_rising_min_count to 0 so the rising-MA confirmation never "
                "rejects."
            ),
            config_overrides={"ma_rising_min_count": 0},
        ),
        AblationVariant(
            name="widen_pullback_range",
            description=(
                "Expand the MA pullback band to ±99% so the pullback location "
                "check always matches."
            ),
            config_overrides={
                "pullback_lower_pct": -99.0,
                "pullback_upper_pct": 99.0,
            },
        ),
    ),
)
