"""Monte Carlo validation helpers for backtest trade PnL sequences."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import mean
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class BacktestMonteCarloConfig:
    runs: int = 1000
    seed: int | None = None
    initial_capital: float = 10_000_000.0
    ruin_drawdown_pct: float = 30.0

    def __post_init__(self) -> None:
        if self.runs <= 0:
            raise ValueError("runs must be positive")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.ruin_drawdown_pct <= 0:
            raise ValueError("ruin_drawdown_pct must be positive")


@dataclass(frozen=True)
class TradePathMetrics:
    final_equity: float
    max_drawdown: float
    max_drawdown_pct: float
    longest_losing_streak: int


@dataclass(frozen=True)
class BacktestMonteCarloResult:
    runs: int
    trade_count: int
    seed: int | None
    initial_capital: float
    ruin_drawdown_pct: float
    ruin_probability: float
    worst_max_drawdown: float
    worst_max_drawdown_pct: float
    worst_losing_streak: int
    avg_final_equity: float
    p05_final_equity: float
    p50_final_equity: float
    p95_final_equity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "trade_count": self.trade_count,
            "seed": self.seed,
            "initial_capital": self.initial_capital,
            "ruin_drawdown_pct": self.ruin_drawdown_pct,
            "ruin_probability": self.ruin_probability,
            "worst_max_drawdown": self.worst_max_drawdown,
            "worst_max_drawdown_pct": self.worst_max_drawdown_pct,
            "worst_losing_streak": self.worst_losing_streak,
            "avg_final_equity": self.avg_final_equity,
            "p05_final_equity": self.p05_final_equity,
            "p50_final_equity": self.p50_final_equity,
            "p95_final_equity": self.p95_final_equity,
        }


def extract_net_pnls_from_journal(records: Sequence[Mapping[str, Any]]) -> list[float]:
    pnls: list[float] = []
    completed_statuses = {"SOLD", "ROUND_TRIP", "CLOSED"}
    for record in records:
        if str(record.get("status") or "").upper() not in completed_statuses:
            continue
        pnl = _to_float(record.get("net_pnl"))
        if pnl is not None:
            pnls.append(pnl)
    return pnls


def calculate_trade_path_metrics(
    trade_net_pnls: Sequence[float],
    *,
    initial_capital: float,
) -> TradePathMetrics:
    equity = float(initial_capital)
    peak = equity
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    current_losing_streak = 0
    longest_losing_streak = 0

    for pnl in trade_net_pnls:
        equity += float(pnl)
        if equity > peak:
            peak = equity
        drawdown = max(0.0, peak - equity)
        drawdown_pct = (drawdown / peak * 100.0) if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        if pnl < 0:
            current_losing_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_losing_streak)
        else:
            current_losing_streak = 0

    return TradePathMetrics(
        final_equity=equity,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        longest_losing_streak=longest_losing_streak,
    )


class BacktestMonteCarloSimulator:
    def __init__(self, config: BacktestMonteCarloConfig) -> None:
        self._config = config

    def run(self, trade_net_pnls: Sequence[float]) -> BacktestMonteCarloResult:
        trades = [float(pnl) for pnl in trade_net_pnls]
        if not trades:
            return BacktestMonteCarloResult(
                runs=0,
                trade_count=0,
                seed=self._config.seed,
                initial_capital=self._config.initial_capital,
                ruin_drawdown_pct=self._config.ruin_drawdown_pct,
                ruin_probability=0.0,
                worst_max_drawdown=0.0,
                worst_max_drawdown_pct=0.0,
                worst_losing_streak=0,
                avg_final_equity=self._config.initial_capital,
                p05_final_equity=self._config.initial_capital,
                p50_final_equity=self._config.initial_capital,
                p95_final_equity=self._config.initial_capital,
            )

        rng = random.Random(self._config.seed)
        metrics: list[TradePathMetrics] = []
        for _ in range(self._config.runs):
            shuffled = trades[:]
            rng.shuffle(shuffled)
            metrics.append(
                calculate_trade_path_metrics(
                    shuffled,
                    initial_capital=self._config.initial_capital,
                )
            )

        final_equities = sorted(item.final_equity for item in metrics)
        ruin_count = sum(
            1 for item in metrics
            if item.max_drawdown_pct >= self._config.ruin_drawdown_pct
        )
        return BacktestMonteCarloResult(
            runs=self._config.runs,
            trade_count=len(trades),
            seed=self._config.seed,
            initial_capital=self._config.initial_capital,
            ruin_drawdown_pct=self._config.ruin_drawdown_pct,
            ruin_probability=ruin_count / self._config.runs,
            worst_max_drawdown=max(item.max_drawdown for item in metrics),
            worst_max_drawdown_pct=max(item.max_drawdown_pct for item in metrics),
            worst_losing_streak=max(item.longest_losing_streak for item in metrics),
            avg_final_equity=mean(final_equities),
            p05_final_equity=_percentile(final_equities, 5),
            p50_final_equity=_percentile(final_equities, 50),
            p95_final_equity=_percentile(final_equities, 95),
        )


def _percentile(sorted_values: Sequence[float], percentile: int) -> float:
    if not sorted_values:
        return 0.0
    index = math.ceil(len(sorted_values) * percentile / 100) - 1
    index = min(max(index, 0), len(sorted_values) - 1)
    return sorted_values[index]


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result
