"""Ablation variants for the O'Neil Squeeze Breakout strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


OSB_VARIANT_NAMES: tuple[str, ...] = (
    "disable_smart_money",
    "disable_execution_strength",
    "disable_market_timing",
    "disable_volume_filter",
)


ONEIL_SQUEEZE_BREAKOUT_ABLATION_PRESET = AblationPreset(
    strategy_key="oneil_squeeze_breakout",
    variants=(
        AblationVariant(
            name="disable_smart_money",
            description=(
                "Zero program-buy thresholds so the smart-money gate always passes "
                "when program-buy data is available."
            ),
            config_overrides={
                "program_to_trade_value_pct": 0.0,
                "program_to_market_cap_pct": 0.0,
            },
        ),
        AblationVariant(
            name="disable_execution_strength",
            description=(
                "Zero execution-strength thresholds so cgld_val checks never reject."
            ),
            config_overrides={
                "execution_strength_min": 0.0,
                "sm_flexible_execution_strength": 0.0,
            },
        ),
        AblationVariant(
            name="disable_market_timing",
            description=(
                "Force is_market_timing_ok to True via a universe-service wrapper."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
        AblationVariant(
            name="disable_volume_filter",
            description=(
                "Zero baseline / morning / breakout volume ratios so volume gates "
                "never reject."
            ),
            config_overrides={
                "baseline_min_vol_ratio": 0.0,
                "morning_min_vol_ratio": 0.0,
                "volume_breakout_multiplier": 0.0,
            },
        ),
    ),
)
