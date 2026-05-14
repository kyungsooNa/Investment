"""Strategy performance degradation analysis from standard journal records.

The module is intentionally pure: callers provide live/backtest journal records
and receive metrics/candidates without any service or filesystem dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class StrategyPerformanceDegradationConfig:
    window_size: int = 20
    min_live_trades: int = 10
    min_baseline_trades: int = 10
    capital_base_won: Optional[float] = None
    warn_win_rate_drop_pctp: float = 15.0
    warn_avg_return_drop_pctp: float = 1.0
    warn_profit_factor_below: Optional[float] = 1.0
    critical_consecutive_losses: Optional[int] = 5
    critical_mdd_ratio_multiplier: Optional[float] = 2.0


def compute_strategy_window_metrics(
    records: Iterable[Mapping[str, Any]],
    *,
    window_size: int = 20,
    capital_base_won: Optional[float] = None,
) -> dict[str, dict[str, Any]]:
    """Compute recent-window metrics per strategy from SOLD journal records."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if str(record.get("status") or "").upper() != "SOLD":
            continue
        strategy = str(record.get("strategy") or "").strip()
        if not strategy:
            continue
        grouped.setdefault(strategy, []).append(record)

    result: dict[str, dict[str, Any]] = {}
    for strategy, items in grouped.items():
        ordered = sorted(items, key=_closed_time_key)
        window = ordered[-max(int(window_size or 0), 1):]
        result[strategy] = _compute_window_metrics(window, capital_base_won=capital_base_won)
    return result


def analyze_strategy_performance_degradation(
    live_records: Iterable[Mapping[str, Any]],
    baseline_records: Iterable[Mapping[str, Any]],
    config: StrategyPerformanceDegradationConfig | None = None,
) -> dict[str, Any]:
    """Compare live recent-window metrics with baseline and return soft candidates."""
    cfg = config or StrategyPerformanceDegradationConfig()
    live_metrics = compute_strategy_window_metrics(
        live_records,
        window_size=cfg.window_size,
        capital_base_won=cfg.capital_base_won,
    )
    baseline_metrics = compute_strategy_window_metrics(
        baseline_records,
        window_size=cfg.window_size,
        capital_base_won=cfg.capital_base_won,
    )

    strategies = sorted(set(live_metrics) | set(baseline_metrics))
    by_strategy: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    for strategy in strategies:
        live = live_metrics.get(strategy, _empty_metrics())
        baseline = baseline_metrics.get(strategy, _empty_metrics())

        if live["trade_count"] < cfg.min_live_trades:
            status = "insufficient_live"
            reasons = ["insufficient_live"]
        elif baseline["trade_count"] < cfg.min_baseline_trades:
            status = "insufficient_baseline"
            reasons = ["insufficient_baseline"]
        else:
            status, reasons = _classify_degradation(live, baseline, cfg)

        recommended_actions = _recommended_actions(status)
        item = {
            "strategy": strategy,
            "status": status,
            "reasons": reasons,
            "live_metrics": live,
            "baseline_metrics": baseline,
            "divergence": _build_divergence(live, baseline, cfg),
            "recommended_actions": recommended_actions,
        }
        by_strategy[strategy] = item
        if status in {"degraded", "critical_candidate"}:
            candidates.append(item)

    candidates.sort(key=lambda item: (0 if item["status"] == "critical_candidate" else 1, item["strategy"]))
    return {
        "window_size": cfg.window_size,
        "strategies": by_strategy,
        "candidates": candidates,
    }


def _compute_window_metrics(
    records: list[Mapping[str, Any]],
    *,
    capital_base_won: Optional[float],
) -> dict[str, Any]:
    net_pnls = [_to_float(record.get("net_pnl")) or 0.0 for record in records]
    net_returns = [_to_float(record.get("net_return")) for record in records]
    valid_returns = [value for value in net_returns if value is not None]
    wins_pnl = [value for value in net_pnls if value > 0]
    losses_pnl = [value for value in net_pnls if value < 0]
    win_returns = [value for value in valid_returns if value > 0]
    loss_returns = [value for value in valid_returns if value < 0]
    mfe_values = [_to_float(record.get("mfe")) for record in records]
    mae_values = [_to_float(record.get("mae")) for record in records]
    valid_mfe = [value for value in mfe_values if value is not None]
    valid_mae = [value for value in mae_values if value is not None]
    mdd_amount = _mdd_amount(net_pnls)
    capital_base = _resolve_capital_base(records, capital_base_won)

    return {
        "trade_count": len(records),
        "win_count": len(wins_pnl),
        "loss_count": len(losses_pnl),
        "win_rate": _safe_div(len(wins_pnl), len(records), default=0.0),
        "avg_net_return": _avg(valid_returns, default=0.0),
        "total_net_pnl": sum(net_pnls),
        "payoff_ratio": _payoff_ratio(win_returns, loss_returns),
        "profit_factor": _profit_factor(wins_pnl, losses_pnl),
        "mdd_amount": mdd_amount,
        "mdd_ratio": _safe_div(mdd_amount, capital_base) if capital_base else None,
        "max_consecutive_losses": _max_consecutive_losses(net_pnls),
        "avg_mfe": _avg(valid_mfe) if valid_mfe else None,
        "avg_mae": _avg(valid_mae) if valid_mae else None,
        "missing_mfe_count": sum(1 for value in mfe_values if value is None),
        "missing_mae_count": sum(1 for value in mae_values if value is None),
    }


def _classify_degradation(
    live: Mapping[str, Any],
    baseline: Mapping[str, Any],
    cfg: StrategyPerformanceDegradationConfig,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    critical = False

    win_rate_drop_pctp = (float(baseline["win_rate"]) - float(live["win_rate"])) * 100
    if win_rate_drop_pctp >= cfg.warn_win_rate_drop_pctp:
        reasons.append("win_rate_drop")

    avg_return_drop = float(baseline["avg_net_return"]) - float(live["avg_net_return"])
    if avg_return_drop >= cfg.warn_avg_return_drop_pctp:
        reasons.append("avg_return_drop")

    live_profit_factor = live.get("profit_factor")
    if (
        cfg.warn_profit_factor_below is not None
        and live_profit_factor is not None
        and float(live_profit_factor) < cfg.warn_profit_factor_below
    ):
        reasons.append("profit_factor_low")

    if (
        cfg.critical_consecutive_losses is not None
        and int(live.get("max_consecutive_losses") or 0) >= cfg.critical_consecutive_losses
    ):
        reasons.append("consecutive_losses")
        critical = True

    live_mdd_ratio = live.get("mdd_ratio")
    baseline_mdd_ratio = baseline.get("mdd_ratio")
    if (
        cfg.critical_mdd_ratio_multiplier is not None
        and live_mdd_ratio is not None
        and baseline_mdd_ratio not in (None, 0)
        and float(live_mdd_ratio) >= float(baseline_mdd_ratio) * cfg.critical_mdd_ratio_multiplier
    ):
        reasons.append("mdd_ratio_worse")
        critical = True

    if critical:
        return "critical_candidate", reasons
    if reasons:
        return "degraded", reasons
    return "healthy", []


def _build_divergence(
    live: Mapping[str, Any],
    baseline: Mapping[str, Any],
    cfg: StrategyPerformanceDegradationConfig,
) -> dict[str, Any]:
    live_mdd_ratio = live.get("mdd_ratio")
    baseline_mdd_ratio = baseline.get("mdd_ratio")
    return {
        "win_rate_diff_pctp": (float(live["win_rate"]) - float(baseline["win_rate"])) * 100,
        "avg_net_return_diff_pctp": float(live["avg_net_return"]) - float(baseline["avg_net_return"]),
        "profit_factor_diff": _optional_diff(live.get("profit_factor"), baseline.get("profit_factor")),
        "mdd_amount_diff": float(live["mdd_amount"]) - float(baseline["mdd_amount"]),
        "mdd_ratio_diff": (
            _optional_diff(live_mdd_ratio, baseline_mdd_ratio)
            if cfg.capital_base_won is not None
            else None
        ),
    }


def _recommended_actions(status: str) -> list[str]:
    if status == "critical_candidate":
        return [
            "pause_new_entries_candidate",
            "reduce_position_size_candidate",
            "paper_mode_candidate",
        ]
    if status == "degraded":
        return [
            "pause_new_entries_candidate",
            "reduce_position_size_candidate",
        ]
    return []


def _closed_time_key(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("sell_date", "exit_time", "closed_at"):
            value = metadata.get(key)
            if value not in (None, ""):
                return str(value)
    for key in ("sell_date", "exit_time", "closed_at", "signal_time"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _mdd_amount(net_pnls: list[float]) -> float:
    peak = 0.0
    cumulative = 0.0
    mdd = 0.0
    for pnl in net_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > mdd:
            mdd = drawdown
    return mdd


def _max_consecutive_losses(net_pnls: list[float]) -> int:
    current = 0
    best = 0
    for pnl in net_pnls:
        if pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _payoff_ratio(win_returns: list[float], loss_returns: list[float]) -> Optional[float]:
    if not win_returns or not loss_returns:
        return None
    avg_loss = abs(_avg(loss_returns))
    return _safe_div(_avg(win_returns), avg_loss) if avg_loss else None


def _profit_factor(wins_pnl: list[float], losses_pnl: list[float]) -> Optional[float]:
    total_loss = abs(sum(losses_pnl))
    if not wins_pnl or total_loss == 0:
        return None
    return sum(wins_pnl) / total_loss


def _resolve_capital_base(
    records: list[Mapping[str, Any]],
    capital_base_won: Optional[float],
) -> Optional[float]:
    explicit = _to_float(capital_base_won)
    if explicit and explicit > 0:
        return explicit
    for record in records:
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("capital_base_won", "equity", "total_equity"):
                value = _to_float(metadata.get(key))
                if value and value > 0:
                    return value
    return None


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_net_return": 0.0,
        "total_net_pnl": 0.0,
        "payoff_ratio": None,
        "profit_factor": None,
        "mdd_amount": 0.0,
        "mdd_ratio": None,
        "max_consecutive_losses": 0,
        "avg_mfe": None,
        "avg_mae": None,
        "missing_mfe_count": 0,
        "missing_mae_count": 0,
    }


def _avg(values: list[float], default: Optional[float] = None) -> Optional[float]:
    if not values:
        return default
    return sum(values) / len(values)


def _safe_div(numerator: float, denominator: float, default: Optional[float] = None) -> Optional[float]:
    return default if denominator == 0 else numerator / denominator


def _optional_diff(left: Any, right: Any) -> Optional[float]:
    left_value = _to_float(left)
    right_value = _to_float(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
