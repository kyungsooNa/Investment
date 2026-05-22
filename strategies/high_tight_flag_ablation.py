"""Ablation variants for the High Tight Flag (HTF) strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


HTF_VARIANT_NAMES: tuple[str, ...] = (
    "disable_smart_money",
    "disable_execution_strength",
    "disable_market_timing",
    "disable_volume_filter",
    "relax_pattern_check",
)


HIGH_TIGHT_FLAG_ABLATION_PRESET = AblationPreset(
    strategy_key="high_tight_flag",
    variants=(
        AblationVariant(
            name="disable_smart_money",
            description=(
                "Zero the program-to-market-cap threshold so the HTF smart-money "
                "filter always passes."
            ),
            config_overrides={"program_to_market_cap_pct": 0.0},
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
                "Zero breakout / afternoon volume multipliers so volume gates never "
                "reject."
            ),
            config_overrides={
                "volume_breakout_multiplier": 0.0,
                "afternoon_volume_multiplier": 0.0,
            },
        ),
        AblationVariant(
            name="relax_pattern_check",
            description=(
                "Disable pole surge and flag drawdown gates: any candidate with a "
                "qualifying breakout will pass."
            ),
            config_overrides={
                "pole_min_surge_ratio": 0.0,
                "flag_max_drawdown_pct": 999.0,
            },
        ),
    ),
)
