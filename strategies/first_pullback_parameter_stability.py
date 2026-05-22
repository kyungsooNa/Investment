"""Parameter stability sweep for the First Pullback (Holy Grail) strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


FIRST_PULLBACK_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="first_pullback",
    dimensions=(
        StabilitySweepDimension(
            name="pullback_upper_pct",
            parameter="pullback_upper_pct",
            values=(1.0, 2.0, 3.0, 4.0, 5.0),
            baseline_index=2,
            description="눌림목 20MA 상단 허용 폭. baseline +3.0% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="rapid_surge_pct",
            parameter="rapid_surge_pct",
            values=(20.0, 25.0, 30.0, 35.0, 40.0),
            baseline_index=2,
            description="단기 급등 임계. baseline 30% 주변 ±10pp.",
        ),
        StabilitySweepDimension(
            name="execution_strength_min",
            parameter="execution_strength_min",
            values=(80.0, 90.0, 100.0, 110.0, 120.0),
            baseline_index=2,
            description="체결강도 하한. baseline 100% 주변 ±20pp.",
        ),
    ),
)
