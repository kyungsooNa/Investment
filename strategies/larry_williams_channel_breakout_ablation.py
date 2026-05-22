"""Ablation variants for the Larry Williams Channel Breakout strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


CB_VARIANT_NAMES: tuple[str, ...] = (
    "disable_rs_rating",
    "disable_adx_filter",
    "disable_volume_filter",
    "disable_market_timing",
)


LARRY_WILLIAMS_CHANNEL_BREAKOUT_ABLATION_PRESET = AblationPreset(
    strategy_key="larry_williams_channel_breakout",
    variants=(
        AblationVariant(
            name="disable_rs_rating",
            description=(
                "Zero rs_rating_min so the relative-strength floor never rejects."
            ),
            config_overrides={"rs_rating_min": 0},
        ),
        AblationVariant(
            name="disable_adx_filter",
            description=(
                "Zero adx_threshold so the trend-strength gate always passes."
            ),
            config_overrides={"adx_threshold": 0.0},
        ),
        AblationVariant(
            name="disable_volume_filter",
            description=(
                "Zero volume_multiplier so the volume-confirmation gate never "
                "rejects."
            ),
            config_overrides={"volume_multiplier": 0.0},
        ),
        AblationVariant(
            name="disable_market_timing",
            description=(
                "Force is_market_timing_ok to True via a universe-service wrapper."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
    ),
)
