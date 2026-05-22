"""Parameter stability sweep for the O'Neil Squeeze Breakout strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


ONEIL_SQUEEZE_BREAKOUT_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="oneil_squeeze_breakout",
    dimensions=(
        StabilitySweepDimension(
            name="volume_breakout_multiplier",
            parameter="volume_breakout_multiplier",
            values=(1.0, 1.25, 1.5, 1.75, 2.0),
            baseline_index=2,
            description="20일 평균 거래량 대비 돌파 배수. baseline 1.5 주변 ±0.5.",
        ),
        StabilitySweepDimension(
            name="execution_strength_min",
            parameter="execution_strength_min",
            values=(100.0, 110.0, 120.0, 130.0, 140.0),
            baseline_index=2,
            description="체결강도 하한. baseline 120% 주변 ±20pp.",
        ),
        StabilitySweepDimension(
            name="osb_max_extension_pct",
            parameter="osb_max_extension_pct",
            values=(1.0, 1.5, 2.0, 2.5, 3.0),
            baseline_index=2,
            description="돌파 후 최대 추격 허용 %. baseline 2.0% 주변 ±1pp.",
        ),
    ),
)
