"""Parameter stability sweep for the RSI(2) Pullback strategy.

See [todo_list.md](../todo_list.md) section P1-1.
"""
from __future__ import annotations

from services.parameter_stability_service import (
    StabilitySweepDimension,
    StabilitySweepPreset,
)


RSI2_PULLBACK_PARAMETER_STABILITY_PRESET = StabilitySweepPreset(
    strategy_key="rsi2_pullback",
    dimensions=(
        StabilitySweepDimension(
            name="rsi_threshold",
            parameter="rsi_threshold",
            values=(5.0, 7.5, 10.0, 12.5, 15.0),
            baseline_index=2,
            description="진입 허용 RSI 상한. baseline 10 주변 ±5.",
        ),
        StabilitySweepDimension(
            name="hard_stop_pct",
            parameter="hard_stop_pct",
            values=(-7.0, -6.0, -5.0, -4.0, -3.0),
            baseline_index=2,
            description="진입가 대비 칼손절 (%). baseline -5% 주변 ±2pp.",
        ),
        StabilitySweepDimension(
            name="take_profit_ma_period",
            parameter="take_profit_ma_period",
            values=(3, 4, 5, 7, 10),
            baseline_index=2,
            description="익절 기준 이동평균 기간. baseline 5일 주변 (3~10일).",
        ),
    ),
)
