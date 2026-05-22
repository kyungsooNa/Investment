"""Parameter stability sweep for the High Tight Flag (HTF) strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


HIGH_TIGHT_FLAG_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="high_tight_flag",
    dimensions=(
        StabilitySweepDimension(
            name="pole_min_surge_ratio",
            parameter="pole_min_surge_ratio",
            values=(1.6, 1.75, 1.9, 2.05, 2.2),
            baseline_index=2,
            description="깃대 최소 상승 배수. baseline 1.90 주변 ±0.30.",
        ),
        StabilitySweepDimension(
            name="flag_max_drawdown_pct",
            parameter="flag_max_drawdown_pct",
            values=(10.0, 15.0, 20.0, 25.0, 30.0),
            baseline_index=2,
            description="깃발 최대 하락폭. baseline 20% 주변 ±10pp.",
        ),
        StabilitySweepDimension(
            name="volume_breakout_multiplier",
            parameter="volume_breakout_multiplier",
            values=(1.5, 1.75, 2.0, 2.25, 2.5),
            baseline_index=2,
            description="50일 평균거래량 대비 돌파 배수. baseline 2.0 주변 ±0.5.",
        ),
    ),
)
