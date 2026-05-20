"""Strategy profitability gate for live-expansion decisions.

This module is intentionally pure. Callers provide standard journal records
and optional validation summaries, and receive pass/fail decisions without
filesystem, broker, or scheduler dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from services.regime_performance_service import compute_performance_by_regime
from services.strategy_performance_degradation_service import compute_strategy_window_metrics


@dataclass(frozen=True)
class StrategyProfitabilityGateConfig:
    min_trades: int = 30
    min_profit_factor: Optional[float] = 1.2
    min_payoff_ratio: Optional[float] = 1.0
    min_win_rate: Optional[float] = 0.35
    min_avg_net_return: Optional[float] = 0.0
    require_positive_total_net_pnl: bool = True
    max_mdd_pct: Optional[float] = 20.0
    capital_base_won: Optional[float] = None
    max_monte_carlo_ruin_probability: Optional[float] = 0.05
    max_monte_carlo_worst_mdd_pct: Optional[float] = 30.0
    min_regime_trade_count: int = 5
    require_non_negative_regime_pnl: bool = True


def evaluate_strategy_profitability_gate(
    records: Iterable[Mapping[str, Any]],
    config: StrategyProfitabilityGateConfig | None = None,
    *,
    monte_carlo: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether each strategy clears the live-expansion baseline."""
    cfg = config or StrategyProfitabilityGateConfig()
    sold_records = [
        record for record in records
        if str(record.get("status") or "").upper() == "SOLD"
        and str(record.get("strategy") or "").strip()
    ]
    strategy_names = sorted({str(record.get("strategy") or "").strip() for record in sold_records})
    by_strategy: dict[str, dict[str, Any]] = {}

    for strategy in strategy_names:
        strategy_records = [
            record for record in sold_records
            if str(record.get("strategy") or "").strip() == strategy
        ]
        by_strategy[strategy] = _evaluate_one_strategy(
            strategy,
            strategy_records,
            cfg,
            monte_carlo=monte_carlo,
        )

    statuses = [item["status"] for item in by_strategy.values()]
    return {
        "config": _config_to_dict(cfg),
        "summary": {
            "strategy_count": len(by_strategy),
            "pass_count": statuses.count("pass"),
            "fail_count": statuses.count("fail"),
            "insufficient_sample_count": statuses.count("insufficient_sample"),
        },
        "strategies": by_strategy,
    }


def _evaluate_one_strategy(
    strategy: str,
    records: list[Mapping[str, Any]],
    cfg: StrategyProfitabilityGateConfig,
    *,
    monte_carlo: Mapping[str, Any] | None,
) -> dict[str, Any]:
    metrics = compute_strategy_window_metrics(
        records,
        window_size=max(len(records), 1),
        capital_base_won=cfg.capital_base_won,
    ).get(strategy, _empty_metrics())
    metrics["mdd_pct"] = (
        float(metrics["mdd_ratio"]) * 100.0
        if metrics.get("mdd_ratio") is not None
        else None
    )

    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if int(metrics["trade_count"]) < cfg.min_trades:
        blocking_reasons.append("insufficient_trades")
        return _decision(
            strategy=strategy,
            status="insufficient_sample",
            metrics=metrics,
            blocking_reasons=blocking_reasons,
            warnings=warnings,
            regime_performance=compute_performance_by_regime(records),
        )

    _append_metric_failures(metrics, cfg, blocking_reasons, warnings)
    _append_monte_carlo_failures(monte_carlo, cfg, blocking_reasons, warnings)
    regime_performance = compute_performance_by_regime(records)
    _append_regime_failures(regime_performance, cfg, blocking_reasons)

    return _decision(
        strategy=strategy,
        status="fail" if blocking_reasons else "pass",
        metrics=metrics,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        regime_performance=regime_performance,
    )


def _append_metric_failures(
    metrics: Mapping[str, Any],
    cfg: StrategyProfitabilityGateConfig,
    blocking_reasons: list[str],
    warnings: list[str],
) -> None:
    if cfg.min_profit_factor is not None and not _meets_min_optional(
        metrics.get("profit_factor"),
        cfg.min_profit_factor,
        no_loss_pass=metrics.get("win_count", 0) > 0 and metrics.get("loss_count", 0) == 0,
    ):
        blocking_reasons.append("profit_factor_below")

    if cfg.min_payoff_ratio is not None and not _meets_min_optional(
        metrics.get("payoff_ratio"),
        cfg.min_payoff_ratio,
        no_loss_pass=metrics.get("win_count", 0) > 0 and metrics.get("loss_count", 0) == 0,
    ):
        blocking_reasons.append("payoff_ratio_below")

    if cfg.min_win_rate is not None and float(metrics.get("win_rate") or 0.0) < cfg.min_win_rate:
        blocking_reasons.append("win_rate_below")

    if cfg.min_avg_net_return is not None and float(metrics.get("avg_net_return") or 0.0) < cfg.min_avg_net_return:
        blocking_reasons.append("avg_net_return_below")

    if cfg.require_positive_total_net_pnl and float(metrics.get("total_net_pnl") or 0.0) <= 0:
        blocking_reasons.append("total_net_pnl_not_positive")

    mdd_pct = metrics.get("mdd_pct")
    if cfg.max_mdd_pct is not None:
        if mdd_pct is None:
            warnings.append("mdd_pct_unavailable")
        elif float(mdd_pct) > cfg.max_mdd_pct:
            blocking_reasons.append("mdd_pct_above")


def _append_monte_carlo_failures(
    monte_carlo: Mapping[str, Any] | None,
    cfg: StrategyProfitabilityGateConfig,
    blocking_reasons: list[str],
    warnings: list[str],
) -> None:
    if monte_carlo is None:
        if (
            cfg.max_monte_carlo_ruin_probability is not None
            or cfg.max_monte_carlo_worst_mdd_pct is not None
        ):
            warnings.append("monte_carlo_unavailable")
        return

    ruin_probability = _to_float(monte_carlo.get("ruin_probability"))
    if (
        cfg.max_monte_carlo_ruin_probability is not None
        and ruin_probability is not None
        and ruin_probability > cfg.max_monte_carlo_ruin_probability
    ):
        blocking_reasons.append("monte_carlo_ruin_probability_above")

    worst_mdd_pct = _to_float(monte_carlo.get("worst_max_drawdown_pct"))
    if (
        cfg.max_monte_carlo_worst_mdd_pct is not None
        and worst_mdd_pct is not None
        and worst_mdd_pct > cfg.max_monte_carlo_worst_mdd_pct
    ):
        blocking_reasons.append("monte_carlo_worst_mdd_pct_above")


def _append_regime_failures(
    regime_performance: Mapping[str, Mapping[str, Any]],
    cfg: StrategyProfitabilityGateConfig,
    blocking_reasons: list[str],
) -> None:
    if not cfg.require_non_negative_regime_pnl:
        return
    min_count = max(int(cfg.min_regime_trade_count or 0), 1)
    for bucket, metrics in regime_performance.items():
        if int(metrics.get("trade_count") or 0) < min_count:
            continue
        if float(metrics.get("total_net_pnl") or 0.0) < 0:
            blocking_reasons.append(f"regime_{bucket}_negative_pnl")


def _decision(
    *,
    strategy: str,
    status: str,
    metrics: Mapping[str, Any],
    blocking_reasons: list[str],
    warnings: list[str],
    regime_performance: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "status": status,
        "passed": status == "pass",
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "metrics": dict(metrics),
        "regime_performance": dict(regime_performance),
    }


def _meets_min_optional(value: Any, threshold: float, *, no_loss_pass: bool) -> bool:
    if value is None:
        return no_loss_pass
    return float(value) >= threshold


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
        "mdd_pct": None,
        "max_consecutive_losses": 0,
        "avg_mfe": None,
        "avg_mae": None,
        "missing_mfe_count": 0,
        "missing_mae_count": 0,
    }


def _config_to_dict(cfg: StrategyProfitabilityGateConfig) -> dict[str, Any]:
    return {
        field: getattr(cfg, field)
        for field in StrategyProfitabilityGateConfig.__dataclass_fields__
    }


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
