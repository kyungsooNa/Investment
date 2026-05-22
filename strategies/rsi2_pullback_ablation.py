"""Ablation variants for the RSI(2) Pullback strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


RSI2_VARIANT_NAMES: tuple[str, ...] = (
    "disable_minervini_stage2",
    "disable_market_timing",
    "disable_rsi_oversold",
)


RSI2_PULLBACK_ABLATION_PRESET = AblationPreset(
    strategy_key="rsi2_pullback",
    variants=(
        AblationVariant(
            name="disable_minervini_stage2",
            description=(
                "Drop the Stage 2 requirement so non-Stage-2 candidates are not "
                "filtered out."
            ),
            config_overrides={"require_minervini_stage2": False},
        ),
        AblationVariant(
            name="disable_market_timing",
            description=(
                "Force is_market_timing_ok to True via a universe-service wrapper."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
        AblationVariant(
            name="disable_rsi_oversold",
            description=(
                "Raise rsi_threshold to 100 so any RSI value qualifies as oversold."
            ),
            config_overrides={"rsi_threshold": 100.0},
        ),
    ),
)
