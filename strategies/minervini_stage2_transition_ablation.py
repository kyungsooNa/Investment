"""Ablation variants for the Minervini Stage 2 transition strategy (todo 1-8).

Stage2 목록 자체가 유니버스라 universe_service 교체 variant 는 두지 않는다.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


MINERVINI_STAGE2_TRANSITION_ABLATION_PRESET = AblationPreset(
    strategy_key="minervini_stage2_transition",
    variants=(
        AblationVariant(
            name="relax_recent_transition",
            description=(
                "Widen the Stage 2 transition window to the full OHLCV lookback so "
                "long-running Stage 2 names are no longer filtered out."
            ),
            config_overrides={"transition_lookback_days": 260},
        ),
        AblationVariant(
            name="disable_breakout_extension_cap",
            description="Allow entries however far price has run above the 20-day pivot.",
            config_overrides={"max_breakout_extension_pct": 100.0},
        ),
        AblationVariant(
            name="disable_rs_floor",
            description="Drop the RS Rating floor so every Stage 2 name is scanned.",
            config_overrides={"min_rs_rating": 0},
        ),
        AblationVariant(
            name="disable_liquidity_floor",
            description="Drop the trading value floor (current and 20-day average).",
            config_overrides={"min_trading_value": 0},
        ),
    ),
)
