from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.backtest_walk_forward import (
    BacktestWalkForwardConfig,
    BacktestWalkForwardRunner,
    build_walk_forward_validation_metrics,
    build_walk_forward_segments,
)


def test_build_walk_forward_segments_splits_train_tune_test_windows():
    dates = [
        "20260101",
        "20260102",
        "20260103",
        "20260104",
        "20260105",
        "20260106",
        "20260107",
        "20260108",
    ]

    segments = build_walk_forward_segments(
        dates,
        BacktestWalkForwardConfig(train_size=3, tune_size=2, test_size=1, step_size=1),
    )

    assert len(segments) == 3
    assert segments[0].train_dates == ["20260101", "20260102", "20260103"]
    assert segments[0].tune_dates == ["20260104", "20260105"]
    assert segments[0].test_dates == ["20260106"]
    assert segments[1].train_dates == ["20260102", "20260103", "20260104"]
    assert segments[1].tune_dates == ["20260105", "20260106"]
    assert segments[1].test_dates == ["20260107"]
    assert segments[2].train_dates == ["20260103", "20260104", "20260105"]
    assert segments[2].tune_dates == ["20260106", "20260107"]
    assert segments[2].test_dates == ["20260108"]


def test_build_walk_forward_segments_defaults_step_to_test_size():
    dates = ["20260101", "20260102", "20260103", "20260104", "20260105", "20260106"]

    segments = build_walk_forward_segments(
        dates,
        BacktestWalkForwardConfig(train_size=2, tune_size=1, test_size=2),
    )

    assert len(segments) == 2
    assert segments[0].test_dates == ["20260104", "20260105"]
    assert segments[1].train_dates == ["20260103", "20260104"]
    assert segments[1].tune_dates == ["20260105"]
    assert segments[1].test_dates == ["20260106"]


def test_build_walk_forward_segments_applies_embargo_between_tune_and_test():
    dates = [
        "20260101",
        "20260102",
        "20260103",
        "20260104",
        "20260105",
        "20260106",
        "20260107",
        "20260108",
    ]

    segments = build_walk_forward_segments(
        dates,
        BacktestWalkForwardConfig(
            train_size=2,
            tune_size=1,
            test_size=1,
            step_size=1,
            embargo_days=1,
        ),
    )

    assert len(segments) == 4
    assert segments[0].train_dates == ["20260101", "20260102"]
    assert segments[0].tune_dates == ["20260103"]
    # 20260104 is embargoed, so test starts at 20260105.
    assert segments[0].test_dates == ["20260105"]
    assert segments[1].train_dates == ["20260102", "20260103"]
    assert segments[1].tune_dates == ["20260104"]
    assert segments[1].test_dates == ["20260106"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_size": 0, "tune_size": 1, "test_size": 1},
        {"train_size": 1, "tune_size": 0, "test_size": 1},
        {"train_size": 1, "tune_size": 1, "test_size": 0},
        {"train_size": 1, "tune_size": 1, "test_size": 1, "step_size": 0},
        {"train_size": 1, "tune_size": 1, "test_size": 1, "embargo_days": -1},
    ],
)
def test_walk_forward_config_rejects_non_positive_sizes(kwargs):
    with pytest.raises(ValueError):
        BacktestWalkForwardConfig(**kwargs)


@pytest.mark.asyncio
async def test_walk_forward_runner_executes_each_phase_with_isolated_runner():
    calls: list[tuple[str, int, list[str], int]] = []
    created_runner_ids: list[int] = []

    class FakePhaseRunner:
        def __init__(self, runner_id: int, phase: str) -> None:
            self.runner_id = runner_id
            self.phase = phase

        async def run(self, dates):
            calls.append((self.phase, len(calls) // 3, list(dates), self.runner_id))
            return SimpleNamespace(
                portfolio={"realized_net_pnl": 10 if self.phase == "test" else 999},
                execution_reports=[],
                journal_records=[],
            )

    def runner_factory(phase, segment):
        runner_id = len(created_runner_ids) + 1
        created_runner_ids.append(runner_id)
        return FakePhaseRunner(runner_id, phase)

    runner = BacktestWalkForwardRunner(
        runner_factory=runner_factory,
        config=BacktestWalkForwardConfig(train_size=2, tune_size=1, test_size=1, step_size=1),
    )

    result = await runner.run(["20260101", "20260102", "20260103", "20260104", "20260105"])

    assert [call[0] for call in calls] == ["train", "tune", "test", "train", "tune", "test"]
    assert calls[0][2] == ["20260101", "20260102"]
    assert calls[1][2] == ["20260103"]
    assert calls[2][2] == ["20260104"]
    assert calls[3][2] == ["20260102", "20260103"]
    assert calls[4][2] == ["20260104"]
    assert calls[5][2] == ["20260105"]
    assert created_runner_ids == [1, 2, 3, 4, 5, 6]
    assert len(result.segments) == 2


@pytest.mark.asyncio
async def test_walk_forward_summary_aggregates_test_phase_only():
    class FakePhaseRunner:
        def __init__(self, phase: str) -> None:
            self.phase = phase

        async def run(self, dates):
            pnl = {"train": 1000, "tune": 500, "test": -30}[self.phase]
            records = [{"status": "REJECTED"}] if self.phase == "test" else []
            return SimpleNamespace(
                portfolio={"realized_net_pnl": pnl},
                execution_reports=[SimpleNamespace()] if self.phase == "test" else [],
                journal_records=records,
            )

    runner = BacktestWalkForwardRunner(
        runner_factory=lambda phase, segment: FakePhaseRunner(phase),
        config=BacktestWalkForwardConfig(train_size=2, tune_size=1, test_size=1, step_size=1),
    )

    result = await runner.run(["20260101", "20260102", "20260103", "20260104", "20260105"])

    assert result.summary == {
        "segment_count": 2,
        "embargo_days": 0,
        "train_days": 4,
        "tune_days": 2,
        "test_days": 2,
        "test_realized_net_pnl": -60,
        "test_execution_count": 2,
        "test_rejected_count": 2,
        "validation_metrics_by_strategy": {},
    }


def test_build_walk_forward_validation_metrics_aggregates_in_and_out_of_sample_by_strategy():
    segments = [
        SimpleNamespace(
            train_result=SimpleNamespace(
                journal_records=[
                    {"status": "SOLD", "strategy": "S1", "net_pnl": 100},
                    {"status": "SOLD", "strategy": "S2", "net_pnl": 50},
                ]
            ),
            tune_result=SimpleNamespace(
                journal_records=[
                    {"status": "SOLD", "strategy": "S1", "net_pnl": 40},
                    {"status": "REJECTED", "strategy": "S1", "net_pnl": -999},
                ]
            ),
            test_result=SimpleNamespace(
                journal_records=[
                    {"status": "SOLD", "strategy": "S1", "net_pnl": -20},
                    {"status": "SOLD", "strategy": "S2", "net_pnl": 30},
                ]
            ),
        ),
        SimpleNamespace(
            train_result=SimpleNamespace(
                journal_records=[
                    {"status": "SOLD", "strategy": "S1", "net_pnl": 200},
                ]
            ),
            tune_result=SimpleNamespace(journal_records=[]),
            test_result=SimpleNamespace(
                journal_records=[
                    {"status": "SOLD", "strategy": "S1", "net_pnl": 10},
                ]
            ),
        ),
    ]

    metrics = build_walk_forward_validation_metrics(segments)

    assert metrics["S1"] == {
        "walk_forward_segment_count": 2,
        "train_net_pnl": 300.0,
        "tune_net_pnl": 40.0,
        "test_net_pnl": -10.0,
        "in_sample_net_pnl": 340.0,
        "out_of_sample_net_pnl": -10.0,
        "in_sample_trade_count": 3,
        "out_of_sample_trade_count": 2,
        "out_of_sample_positive_segment_count": 1,
        "out_of_sample_positive_segment_ratio": 0.5,
    }
    assert metrics["S2"]["in_sample_net_pnl"] == 50.0
    assert metrics["S2"]["out_of_sample_net_pnl"] == 30.0
