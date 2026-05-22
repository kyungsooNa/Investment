"""Parameter stability sweep for the Larry Williams VBO strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


LARRY_WILLIAMS_VBO_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="larry_williams_vbo",
    dimensions=(
        StabilitySweepDimension(
            name="k_value",
            parameter="k_value",
            values=(0.3, 0.4, 0.5, 0.6, 0.7),
            baseline_index=2,
            description="Range 승수 K. baseline 0.5 주변 ±0.2.",
        ),
        StabilitySweepDimension(
            name="confidence_threshold",
            parameter="confidence_threshold",
            values=(100.0, 110.0, 120.0, 130.0, 140.0),
            baseline_index=2,
            description="스냅샷 체결강도 하한. baseline 120% 주변 ±20pp.",
        ),
        StabilitySweepDimension(
            name="program_buy_ratio",
            parameter="program_buy_ratio",
            values=(0.05, 0.075, 0.10, 0.125, 0.15),
            baseline_index=2,
            description="프로그램 순매수/거래대금 하한. baseline 10% 주변 ±5pp.",
        ),
    ),
)
