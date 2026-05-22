"""Parameter stability sweep for the O'Neil Pocket Pivot / BGU strategy.

Each dimension sweeps a single ``OneilPocketPivotConfig`` field around its
current default to expose single-threshold spikes. See
[todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


ONEIL_POCKET_PIVOT_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="oneil_pocket_pivot",
    dimensions=(
        StabilitySweepDimension(
            name="pp_ma_proximity_upper_pct",
            parameter="pp_ma_proximity_upper_pct",
            values=(2.0, 3.0, 4.0, 5.0, 6.0),
            baseline_index=2,
            description="PP MA proximity 상한. baseline +4.0% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="bgu_gap_pct",
            parameter="bgu_gap_pct",
            values=(2.0, 3.0, 4.0, 5.0, 6.0),
            baseline_index=2,
            description="BGU 갭상승 진입 임계. baseline 4.0% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="execution_strength_min",
            parameter="execution_strength_min",
            values=(100.0, 110.0, 120.0, 130.0, 140.0),
            baseline_index=2,
            description="체결강도 하한. baseline 120% 주변 ±20pp.",
        ),
    ),
)
