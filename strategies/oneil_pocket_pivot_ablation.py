"""Ablation variants for the O'Neil Pocket Pivot / BGU strategy.

Each variant neutralizes a specific gate so a backtest can measure that
gate's contribution to live performance. See [todo_list.md](../todo_list.md)
section P1-1 for context.

Note: ``disable_smart_money`` only zeroes out the threshold values. The
``pg_buy > 0`` data-presence guard inside ``_check_smart_money`` remains
active because it is not threshold-configurable.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


PP_BGU_VARIANT_NAMES: tuple[str, ...] = (
    "disable_smart_money",
    "disable_execution_strength",
    "disable_market_timing",
    "pp_only",
    "bgu_only",
)


ONEIL_POCKET_PIVOT_ABLATION_PRESET = AblationPreset(
    strategy_key="oneil_pocket_pivot",
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
                "bgu_min_pg_tv_pct": 0.0,
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
                "Force is_market_timing_ok to True via a universe-service wrapper. "
                "Strategy code is untouched."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
        AblationVariant(
            name="pp_only",
            description=(
                "Push BGU gap threshold past any realistic gap so only Pocket "
                "Pivot entries fire."
            ),
            config_overrides={"bgu_gap_pct": 9_999.0},
        ),
        AblationVariant(
            name="bgu_only",
            description=(
                "Set PP MA proximity upper bound below -99% so the MA proximity "
                "check never matches and only BGU entries fire."
            ),
            config_overrides={"pp_ma_proximity_upper_pct": -99.0},
        ),
    ),
)
