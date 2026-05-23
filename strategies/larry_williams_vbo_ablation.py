"""Ablation variants for the Larry Williams VBO strategy.

See [todo_list.md](../todo_list.md) section P1-1 for context.
"""
from __future__ import annotations

from services.strategy_ablation_service import AblationPreset, AblationVariant


VBO_VARIANT_NAMES: tuple[str, ...] = (
    "disable_program_buy",
    "disable_confidence_threshold",
    "disable_market_timing",
    "disable_liquidity_filter",
    "universe_generic_liquidity",
    "universe_vbo_volatility",
)


LARRY_WILLIAMS_VBO_ABLATION_PRESET = AblationPreset(
    strategy_key="larry_williams_vbo",
    variants=(
        AblationVariant(
            name="disable_program_buy",
            description=(
                "Zero program_buy_ratio so the program-buy ratio gate always passes."
            ),
            config_overrides={"program_buy_ratio": 0.0},
        ),
        AblationVariant(
            name="disable_confidence_threshold",
            description=(
                "Zero confidence_threshold so execution-strength snapshot never "
                "rejects."
            ),
            config_overrides={"confidence_threshold": 0.0},
        ),
        AblationVariant(
            name="disable_market_timing",
            description=(
                "Force is_market_timing_ok to True via a universe-service wrapper."
            ),
            universe_overrides={"force_market_timing_ok": True},
        ),
        AblationVariant(
            name="disable_liquidity_filter",
            description=(
                "Zero market-cap and 5-day trading-value floors so liquidity "
                "filtering does not reject candidates."
            ),
            config_overrides={
                "min_market_cap": 0,
                "min_5d_trading_value": 0,
            },
        ),
        AblationVariant(
            name="universe_generic_liquidity",
            description=(
                "Swap the Oneil universe for a generic liquidity-only universe "
                "(5d avg trading value + market-cap floors only; no Pool A "
                "premium analysis, no RS Rating, no Minervini stage, no 52w-high "
                "proximity). Measures how much VBO performance depends on the "
                "O'Neil universe vs a broad liquidity-only candidate set."
            ),
            universe_overrides={"universe_type": "generic_liquidity"},
        ),
        AblationVariant(
            name="universe_vbo_volatility",
            description=(
                "Swap the Oneil universe for a VBO short-term-volatility "
                "specific universe (5d avg trading value + market-cap floors + "
                "20-day annualized volatility ≥ 0.35). 변동성 floor 가 VBO 의 "
                "(open + range × k) 돌파 신호 빈도와 edge 가 충분한 후보로 "
                "사전 선별하는지 측정한다."
            ),
            universe_overrides={"universe_type": "vbo_volatility"},
        ),
    ),
)
