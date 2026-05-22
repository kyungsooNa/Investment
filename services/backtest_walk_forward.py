"""Walk-forward validation runner for period backtests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


class BacktestPhaseRunner(Protocol):
    async def run(self, dates: Sequence[str]) -> Any:
        ...


@dataclass(frozen=True)
class BacktestWalkForwardConfig:
    train_size: int
    tune_size: int
    test_size: int
    step_size: int | None = None
    embargo_days: int = 0

    def __post_init__(self) -> None:
        for field_name in ("train_size", "tune_size", "test_size"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.step_size is not None and self.step_size <= 0:
            raise ValueError("step_size must be positive")
        if self.embargo_days < 0:
            raise ValueError("embargo_days must be zero or positive")

    @property
    def normalized_step_size(self) -> int:
        return self.step_size or self.test_size


@dataclass(frozen=True)
class BacktestWalkForwardSegment:
    index: int
    train_dates: list[str]
    tune_dates: list[str]
    test_dates: list[str]
    train_result: Any | None = None
    tune_result: Any | None = None
    test_result: Any | None = None


@dataclass(frozen=True)
class BacktestWalkForwardRunResult:
    segments: list[BacktestWalkForwardSegment] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    monte_carlo: dict[str, Any] | None = None


BacktestPhaseRunnerFactory = Callable[[str, BacktestWalkForwardSegment], BacktestPhaseRunner]


def build_walk_forward_segments(
    dates: Sequence[str],
    config: BacktestWalkForwardConfig,
) -> list[BacktestWalkForwardSegment]:
    ordered_dates = list(dates)
    segments: list[BacktestWalkForwardSegment] = []
    start = 0
    min_train_tune_end = config.train_size + config.tune_size

    while start + min_train_tune_end + config.embargo_days < len(ordered_dates):
        train_end = start + config.train_size
        tune_end = train_end + config.tune_size
        test_start = tune_end + config.embargo_days
        test_end = min(test_start + config.test_size, len(ordered_dates))
        test_dates = ordered_dates[test_start:test_end]
        if not test_dates:
            break

        segments.append(
            BacktestWalkForwardSegment(
                index=len(segments),
                train_dates=ordered_dates[start:train_end],
                tune_dates=ordered_dates[train_end:tune_end],
                test_dates=test_dates,
            )
        )
        start += config.normalized_step_size

    return segments


class BacktestWalkForwardRunner:
    def __init__(
        self,
        *,
        runner_factory: BacktestPhaseRunnerFactory,
        config: BacktestWalkForwardConfig,
    ) -> None:
        self._runner_factory = runner_factory
        self._config = config

    async def run(self, dates: Sequence[str]) -> BacktestWalkForwardRunResult:
        executed_segments: list[BacktestWalkForwardSegment] = []
        for segment in build_walk_forward_segments(dates, self._config):
            train_result = await self._runner_factory("train", segment).run(segment.train_dates)
            tune_result = await self._runner_factory("tune", segment).run(segment.tune_dates)
            test_result = await self._runner_factory("test", segment).run(segment.test_dates)
            executed_segments.append(
                BacktestWalkForwardSegment(
                    index=segment.index,
                    train_dates=segment.train_dates,
                    tune_dates=segment.tune_dates,
                    test_dates=segment.test_dates,
                    train_result=train_result,
                    tune_result=tune_result,
                    test_result=test_result,
                )
            )

        return BacktestWalkForwardRunResult(
            segments=executed_segments,
            summary=self._build_summary(executed_segments),
        )

    def _build_summary(self, segments: Sequence[BacktestWalkForwardSegment]) -> dict[str, Any]:
        test_realized_net_pnl = 0
        test_execution_count = 0
        test_rejected_count = 0

        for segment in segments:
            result = segment.test_result
            portfolio = getattr(result, "portfolio", None) or {}
            test_realized_net_pnl += portfolio.get("realized_net_pnl", 0) or 0
            test_execution_count += len(getattr(result, "execution_reports", []) or [])
            test_rejected_count += sum(
                1
                for record in (getattr(result, "journal_records", []) or [])
                if record.get("status") == "REJECTED"
            )

        return {
            "segment_count": len(segments),
            "embargo_days": self._config.embargo_days,
            "train_days": sum(len(segment.train_dates) for segment in segments),
            "tune_days": sum(len(segment.tune_dates) for segment in segments),
            "test_days": sum(len(segment.test_dates) for segment in segments),
            "test_realized_net_pnl": test_realized_net_pnl,
            "test_execution_count": test_execution_count,
            "test_rejected_count": test_rejected_count,
        }
