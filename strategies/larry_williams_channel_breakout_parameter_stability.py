"""Parameter stability sweep for the Larry Williams Channel Breakout strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


LARRY_WILLIAMS_CHANNEL_BREAKOUT_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="larry_williams_channel_breakout",
    dimensions=(
        StabilitySweepDimension(
            name="adx_threshold",
            parameter="adx_threshold",
            values=(15.0, 20.0, 25.0, 30.0, 35.0),
            baseline_index=2,
            description="ADX 최소값. baseline 25 주변 ±10pp.",
        ),
        StabilitySweepDimension(
            name="volume_multiplier",
            parameter="volume_multiplier",
            values=(1.0, 1.25, 1.5, 1.75, 2.0),
            baseline_index=2,
            description="20일 평균 거래량 대비 돌파 배수. baseline 1.5 주변 ±0.5.",
        ),
        StabilitySweepDimension(
            name="rs_rating_min",
            parameter="rs_rating_min",
            values=(60, 70, 80, 90, 95),
            baseline_index=2,
            description="RS Rating 최소값. baseline 80 주변.",
        ),
    ),
)
