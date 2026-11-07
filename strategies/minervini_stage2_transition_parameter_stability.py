"""Parameter stability sweep for the Minervini Stage 2 transition strategy (todo 1-8)."""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


MINERVINI_STAGE2_TRANSITION_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="minervini_stage2_transition",
    dimensions=(
        StabilitySweepDimension(
            name="take_profit_pct",
            parameter="take_profit_pct",
            values=(2.0, 3.0, 4.0, 5.0, 6.0),
            baseline_index=2,
            description="진입가 대비 목표 수익 (%). baseline 4% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="hard_stop_pct",
            parameter="hard_stop_pct",
            values=(-7.0, -6.0, -5.0, -4.0, -3.0),
            baseline_index=2,
            description="진입가 대비 칼손절 (%). baseline -5% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="max_holding_days",
            parameter="max_holding_days",
            values=(3, 4, 5, 7, 10),
            baseline_index=2,
            description="시간 청산 거래일 수. baseline 5일 주변 (3~10일).",
        ),
        StabilitySweepDimension(
            name="transition_lookback_days",
            parameter="transition_lookback_days",
            values=(5, 7, 10, 15, 20),
            baseline_index=2,
            description="Stage 2 전환 인정 기간. baseline 10거래일 주변.",
        ),
    ),
)
