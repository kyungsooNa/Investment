"""Strategy profitability gate for live-expansion decisions.

This module is intentionally pure. Callers provide standard journal records
and optional validation summaries, and receive pass/fail decisions without
filesystem, broker, or scheduler dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from services.regime_performance_service import (
    compute_performance_by_regime,
    compute_regime_balance_summary,
)
from services.market_beta_service import compute_market_beta_summary
from services.strategy_performance_degradation_service import compute_strategy_window_metrics
from services.multiple_testing_bias_service import compute_multiple_testing_bias_summary
from services.portfolio_cooldown_service import compute_portfolio_cooldown_summary
from services.portfolio_entry_pressure_service import compute_portfolio_entry_pressure_summary
from services.strategy_correlation_service import compute_strategy_correlation_summary


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
    block_parameter_stability_flags: Sequence[str] = ("spike", "cliff")
    require_parameter_stability: bool = False
    regime_balance_required_buckets: Sequence[str] = (
        "KOSPI_BULL",
        "KOSDAQ_BULL",
        "SIDEWAYS",
        "BEAR",
    )
    regime_balance_min_trades: int = 5
    multiple_testing_min_trials: int = 5
    multiple_testing_top_to_median_warning_ratio: float = 3.0
    multiple_testing_primary_metric: str = "total_net_pnl"
    strategy_correlation_min_overlap: int = 5
    strategy_correlation_warning_threshold: float = 0.8
    strategy_correlation_metric: str = "net_return"
    market_beta_min_overlap: int = 5
    market_beta_warning_threshold: float = 1.5
    market_beta_metric: str = "net_return"
    market_beta_benchmark_metric: str = "market_return"
    daily_entry_warning_threshold: int = 5
    opening_entry_warning_threshold: int = 3
    closing_entry_warning_threshold: int = 3
    consecutive_loss_warning_threshold: int = 3


def evaluate_strategy_profitability_gate(
    records: Iterable[Mapping[str, Any]],
    config: StrategyProfitabilityGateConfig | None = None,
    *,
    monte_carlo: Mapping[str, Any] | None = None,
    parameter_stability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether each strategy clears the live-expansion baseline."""
    cfg = config or StrategyProfitabilityGateConfig()
    all_records = list(records)
    sold_records = [
        record for record in all_records
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
            parameter_stability=parameter_stability,
        )

    statuses = [item["status"] for item in by_strategy.values()]
    metrics_by_strategy = {
        strategy: item.get("metrics", {})
        for strategy, item in by_strategy.items()
    }
    multiple_testing_bias = compute_multiple_testing_bias_summary(
        metrics_by_strategy,
        min_trials=cfg.multiple_testing_min_trials,
        top_to_median_warning_ratio=cfg.multiple_testing_top_to_median_warning_ratio,
        primary_metric=cfg.multiple_testing_primary_metric,
    )
    strategy_correlation = compute_strategy_correlation_summary(
        sold_records,
        min_overlap=cfg.strategy_correlation_min_overlap,
        warning_threshold=cfg.strategy_correlation_warning_threshold,
        metric=cfg.strategy_correlation_metric,
    )
    market_beta = compute_market_beta_summary(
        sold_records,
        min_overlap=cfg.market_beta_min_overlap,
        warning_threshold=cfg.market_beta_warning_threshold,
        metric=cfg.market_beta_metric,
        benchmark_metric=cfg.market_beta_benchmark_metric,
    )
    entry_pressure = compute_portfolio_entry_pressure_summary(
        all_records,
        daily_entry_warning_threshold=cfg.daily_entry_warning_threshold,
        opening_entry_warning_threshold=cfg.opening_entry_warning_threshold,
        closing_entry_warning_threshold=cfg.closing_entry_warning_threshold,
    )
    cooldown = compute_portfolio_cooldown_summary(
        sold_records,
        consecutive_loss_warning_threshold=cfg.consecutive_loss_warning_threshold,
    )
    warnings = []
    if multiple_testing_bias.get("bias_warning"):
        warnings.append("multiple_testing_bias_warning")
    warnings.extend(strategy_correlation.get("warnings") or [])
    warnings.extend(market_beta.get("warnings") or [])
    warnings.extend(entry_pressure.get("warnings") or [])
    warnings.extend(cooldown.get("warnings") or [])
    return {
        "config": _config_to_dict(cfg),
        "summary": {
            "strategy_count": len(by_strategy),
            "pass_count": statuses.count("pass"),
            "fail_count": statuses.count("fail"),
            "insufficient_sample_count": statuses.count("insufficient_sample"),
        },
        "warnings": warnings,
        "multiple_testing_bias": multiple_testing_bias,
        "strategy_correlation": strategy_correlation,
        "market_beta": market_beta,
        "entry_pressure": entry_pressure,
        "cooldown": cooldown,
        "strategies": by_strategy,
    }


def _evaluate_one_strategy(
    strategy: str,
    records: list[Mapping[str, Any]],
    cfg: StrategyProfitabilityGateConfig,
    *,
    monte_carlo: Mapping[str, Any] | None,
    parameter_stability: Mapping[str, Any] | None,
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
    regime_balance = _compute_regime_balance_gate(regime_performance, cfg)
    warnings.extend(regime_balance["warnings"])
    stability_gate = _extract_parameter_stability_gate(parameter_stability, cfg)
    blocking_reasons.extend(stability_gate["blocking_reasons"])
    warnings.extend(stability_gate["warnings"])

    return _decision(
        strategy=strategy,
        status="fail" if blocking_reasons else "pass",
        metrics=metrics,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        regime_performance=regime_performance,
        regime_balance=regime_balance,
        parameter_stability=stability_gate,
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


def _compute_regime_balance_gate(
    regime_performance: Mapping[str, Mapping[str, Any]],
    cfg: StrategyProfitabilityGateConfig,
) -> dict[str, Any]:
    required = tuple(cfg.regime_balance_required_buckets or ())
    if not required:
        return {
            "enabled": False,
            "balanced_pass": True,
            "warnings": [],
        }

    summary = compute_regime_balance_summary(
        regime_performance,
        required_buckets=required,
        min_trades_per_bucket=cfg.regime_balance_min_trades,
    )
    warnings = [] if summary["balanced_pass"] else ["regime_balance_incomplete"]
    return {
        "enabled": True,
        **summary,
        "warnings": warnings,
    }


def _extract_parameter_stability_gate(
    parameter_stability: Mapping[str, Any] | None,
    cfg: StrategyProfitabilityGateConfig,
) -> dict[str, Any]:
    blocked_flags = {
        str(flag).strip().lower()
        for flag in (cfg.block_parameter_stability_flags or ())
        if str(flag).strip()
    }
    payload = _unwrap_parameter_stability_summary(parameter_stability)
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    issues: list[dict[str, Any]] = []

    if payload is None:
        if cfg.require_parameter_stability:
            blocking_reasons.append("parameter_stability_unavailable")
        return {
            "available": False,
            "blocked_flags": sorted(blocked_flags),
            "issues": issues,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
        }

    dimensions = payload.get("dimensions") or {}
    if not dimensions:
        warnings.append("parameter_stability_empty")

    for dimension_name, dimension_payload in dimensions.items():
        stability = (dimension_payload or {}).get("stability") or {}
        flag = str(stability.get("flag") or "").strip().lower()
        if not flag or flag not in blocked_flags:
            continue
        issues.append(
            {
                "dimension": str(dimension_name),
                "flag": flag,
                "reason": stability.get("reason"),
            }
        )
        blocking_reasons.append(f"parameter_stability_{flag}:{dimension_name}")

    return {
        "available": True,
        "blocked_flags": sorted(blocked_flags),
        "issues": issues,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def _unwrap_parameter_stability_summary(
    parameter_stability: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not parameter_stability:
        return None
    summary = parameter_stability.get("summary")
    if isinstance(summary, Mapping):
        return summary
    return parameter_stability


def _decision(
    *,
    strategy: str,
    status: str,
    metrics: Mapping[str, Any],
    blocking_reasons: list[str],
    warnings: list[str],
    regime_performance: Mapping[str, Mapping[str, Any]],
    regime_balance: Mapping[str, Any] | None = None,
    parameter_stability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "status": status,
        "passed": status == "pass",
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "metrics": dict(metrics),
        "regime_performance": dict(regime_performance),
        "regime_balance": dict(regime_balance or {}),
        "parameter_stability": dict(parameter_stability or {}),
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
