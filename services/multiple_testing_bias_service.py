"""Lightweight multiple-testing bias report helpers.

This module exposes conservative proxies (PBO, adjusted-Sharpe haircut) plus a
formal Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014). They are intended
to turn strategy-selection red flags into a stable gate contract without
requiring a full walk-forward research stack.
"""
from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist, median, pstdev, variance
from typing import Any, Mapping, Sequence


def compute_multiple_testing_bias_summary(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    min_trials: int = 5,
    top_to_median_warning_ratio: float = 3.0,
    primary_metric: str = "total_net_pnl",
    min_adjusted_sharpe: float | None = None,
    max_pbo_probability: float | None = None,
    sharpe_metric: str = "sharpe_ratio",
    in_sample_metric: str = "in_sample_net_pnl",
    out_of_sample_metric: str = "out_of_sample_net_pnl",
    min_deflated_sharpe_probability: float | None = None,
    sample_size_metric: str = "trade_count",
    skew_metric: str = "return_skew",
    kurtosis_metric: str = "return_kurtosis",
    returns_matrix: Sequence[Sequence[float]] | None = None,
    pbo_cscv_splits: int = 16,
    max_pbo_cscv_probability: float | None = None,
) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        value = _to_float(metrics.get(primary_metric))
        if value is None:
            continue
        rows.append((str(strategy), value))

    rows.sort(key=lambda item: item[1], reverse=True)
    trial_count = len(rows)
    warning_reasons: list[str] = []

    best_strategy = rows[0][0] if rows else None
    best_value = rows[0][1] if rows else None
    median_value = float(median([value for _, value in rows])) if rows else None
    ratio = None

    if trial_count >= max(int(min_trials or 0), 1) and best_value is not None:
        if median_value is not None and median_value > 0:
            ratio = best_value / median_value
            if ratio >= float(top_to_median_warning_ratio):
                warning_reasons.append("best_over_median_ratio_high")
        elif best_value > 0:
            warning_reasons.append("best_positive_median_non_positive")

    deflated_sharpe = _compute_deflated_sharpe_proxy(
        metrics_by_strategy,
        trial_count=trial_count,
        min_trials=min_trials,
        threshold=min_adjusted_sharpe,
        metric=sharpe_metric,
    )
    if deflated_sharpe.get("available") and deflated_sharpe.get("passed") is False:
        warning_reasons.append("deflated_sharpe_below_threshold")

    pbo = _compute_pbo_proxy(
        metrics_by_strategy,
        min_trials=min_trials,
        threshold=max_pbo_probability,
        in_sample_metric=in_sample_metric,
        out_of_sample_metric=out_of_sample_metric,
    )
    if pbo.get("available") and pbo.get("passed") is False:
        warning_reasons.append("pbo_probability_above_threshold")

    deflated_sharpe_formal = _compute_deflated_sharpe(
        metrics_by_strategy,
        trial_count=trial_count,
        min_trials=min_trials,
        threshold=min_deflated_sharpe_probability,
        metric=sharpe_metric,
        sample_size_metric=sample_size_metric,
        skew_metric=skew_metric,
        kurtosis_metric=kurtosis_metric,
    )
    if deflated_sharpe_formal.get("available") and deflated_sharpe_formal.get("passed") is False:
        warning_reasons.append("deflated_sharpe_probability_below_threshold")

    if returns_matrix is not None:
        pbo_cscv = compute_pbo_cscv(
            returns_matrix,
            n_splits=pbo_cscv_splits,
            threshold=max_pbo_cscv_probability,
        )
    else:
        pbo_cscv = {"available": False, "reason": "not_provided", "passed": None}
    if pbo_cscv.get("available") and pbo_cscv.get("passed") is False:
        warning_reasons.append("pbo_cscv_above_threshold")

    return {
        "trial_count": trial_count,
        "primary_metric": primary_metric,
        "best_strategy": best_strategy,
        "best_value": best_value,
        "median_value": median_value,
        "top_to_median_ratio": round(ratio, 3) if ratio is not None else None,
        "warning_reasons": warning_reasons,
        "bias_warning": bool(warning_reasons),
        "rankings": [
            {"strategy": strategy, primary_metric: value}
            for strategy, value in rows
        ],
        "deflated_sharpe_proxy": deflated_sharpe,
        "deflated_sharpe": deflated_sharpe_formal,
        "pbo_proxy": pbo,
        "pbo_cscv": pbo_cscv,
    }


def _extract_period_key(value: Any) -> str | None:
    """signal_time/날짜 문자열에서 YYYYMMDD 기간 키 추출 (ISO·compact 모두 허용)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def build_config_period_pnl_matrix(
    records_by_config: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    completed_statuses: tuple[str, ...] = ("SOLD", "ROUND_TRIP", "CLOSED"),
    date_key: str = "signal_time",
    value_key: str = "net_pnl",
) -> tuple[list[list[float]], list[str], list[str]]:
    """config별 journal records → CSCV용 ``T x N`` (기간 x config) net_pnl 행렬.

    각 config의 완료 거래를 청산일(``date_key`` 의 YYYYMMDD)별로 묶어 ``value_key``
    합을 구하고, 모든 config의 기간 합집합을 시간순 정렬해 공통 인덱스로 정렬한다.
    해당 기간에 거래가 없는 config 셀은 0.0. 반환: (matrix, config_names, periods).

    ``compute_pbo_cscv`` 의 입력 형태(행=기간, 열=config)와 정확히 일치한다.
    """
    config_names = list(records_by_config.keys())
    statuses = {s.upper() for s in completed_statuses}
    per_config: dict[str, dict[str, float]] = {}
    all_periods: set[str] = set()
    for name in config_names:
        bucket: dict[str, float] = {}
        for record in records_by_config[name] or []:
            if str(record.get("status") or "").upper() not in statuses:
                continue
            period = _extract_period_key(record.get(date_key))
            if period is None:
                continue
            pnl = _to_float(record.get(value_key))
            if pnl is None:
                continue
            bucket[period] = bucket.get(period, 0.0) + pnl
            all_periods.add(period)
        per_config[name] = bucket

    periods = sorted(all_periods)
    matrix = [
        [per_config[name].get(period, 0.0) for name in config_names]
        for period in periods
    ]
    return matrix, config_names, periods


def compute_pbo_cscv(
    returns_matrix: Sequence[Sequence[float]],
    *,
    n_splits: int = 16,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Formal Probability of Backtest Overfitting via CSCV.

    Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de Prado,
    Zhu, 2014). Unlike ``_compute_pbo_proxy`` (a single IS/OOS split heuristic on
    per-strategy scalars), this consumes a full ``T x N`` matrix — T time periods
    (rows) x N candidate configs (columns) of per-period returns — and asks: when
    we pick the in-sample best config across every symmetric IS/OOS partition, how
    often does it land in the bottom half out-of-sample?

    PBO = P(logit(OOS relative rank of the IS-best config) <= 0). High PBO (→0.5+)
    means selection is driven by overfitting; a genuine edge keeps the IS-best near
    the OOS top (PBO → 0).

    The matrix is split into ``n_splits`` (S, even) contiguous equal blocks; we
    evaluate all C(S, S/2) ways of choosing S/2 blocks as in-sample. Block-level
    sufficient statistics keep this cheap even for S=16 (12,870 combinations).
    """
    def _unavailable(reason: str) -> dict[str, Any]:
        return {"available": False, "reason": reason, "pbo": None, "passed": None,
                "n_splits": int(n_splits), "threshold": threshold}

    rows = [list(r) for r in (returns_matrix or [])]
    t_periods = len(rows)
    if t_periods == 0:
        return _unavailable("empty_matrix")
    n_configs = len(rows[0])
    if any(len(r) != n_configs for r in rows):
        return _unavailable("ragged_matrix")
    if n_configs < 2:
        return _unavailable("insufficient_configs")
    s = int(n_splits)
    if s < 4 or s % 2 != 0:
        return _unavailable("invalid_n_splits")
    block = t_periods // s
    if block < 2:  # need >=2 obs/block for a sample stdev
        return _unavailable("insufficient_periods")

    # block-level sufficient stats: count, sum, sum-of-squares per (block, config)
    cnt = [0] * s
    bsum = [[0.0] * n_configs for _ in range(s)]
    bsq = [[0.0] * n_configs for _ in range(s)]
    for b in range(s):
        for i in range(b * block, (b + 1) * block):
            cnt[b] += 1
            row = rows[i]
            sums_b, sq_b = bsum[b], bsq[b]
            for j in range(n_configs):
                v = _to_float(row[j]) or 0.0
                sums_b[j] += v
                sq_b[j] += v * v

    def _sharpes(blockset: tuple[int, ...]) -> list[float]:
        n = sum(cnt[b] for b in blockset)
        out = [0.0] * n_configs
        for j in range(n_configs):
            s_sum = sum(bsum[b][j] for b in blockset)
            s_sq = sum(bsq[b][j] for b in blockset)
            mean = s_sum / n
            var = (s_sq - n * mean * mean) / (n - 1) if n > 1 else 0.0
            out[j] = mean / math.sqrt(var) if var > 1e-18 else 0.0
        return out

    all_blocks = range(s)
    overfit = 0
    total = 0
    logits: list[float] = []
    for combo in combinations(all_blocks, s // 2):
        is_set = set(combo)
        oos = tuple(b for b in all_blocks if b not in is_set)
        is_sharpe = _sharpes(combo)
        oos_sharpe = _sharpes(oos)
        n_star = max(range(n_configs), key=lambda j: is_sharpe[j])
        oos_val = oos_sharpe[n_star]
        # OOS rank of the IS-best: 1 = worst .. N = best
        rank = 1 + sum(1 for j in range(n_configs) if oos_sharpe[j] < oos_val)
        omega = rank / (n_configs + 1)
        lam = math.log(omega / (1.0 - omega))
        logits.append(lam)
        if lam <= 0:
            overfit += 1
        total += 1

    pbo = overfit / total if total else None
    passed = None if (threshold is None or pbo is None) else pbo <= float(threshold)
    return {
        "available": True,
        "pbo": round(pbo, 6) if pbo is not None else None,
        "n_configs": n_configs,
        "n_splits": s,
        "n_combinations": total,
        "n_periods_used": block * s,
        "median_logit": round(median(logits), 6) if logits else None,
        "threshold": threshold,
        "passed": passed,
    }


def _compute_deflated_sharpe_proxy(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    trial_count: int,
    min_trials: int,
    threshold: float | None,
    metric: str,
) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        value = _to_float(metrics.get(metric))
        if value is not None:
            rows.append((str(strategy), value))

    rows.sort(key=lambda item: item[1], reverse=True)
    if len(rows) < max(int(min_trials or 0), 1):
        return {
            "available": False,
            "metric": metric,
            "sample_count": len(rows),
            "trial_count": trial_count,
            "threshold": threshold,
            "passed": True,
        }

    best_strategy, best_sharpe = rows[0]
    sharpe_values = [value for _, value in rows]
    dispersion = pstdev(sharpe_values) if len(sharpe_values) > 1 else 0.0
    selection_haircut = math.sqrt(2.0 * math.log(max(len(rows), 1))) * dispersion
    adjusted_sharpe = best_sharpe - selection_haircut
    passed = True if threshold is None else adjusted_sharpe >= float(threshold)

    return {
        "available": True,
        "metric": metric,
        "sample_count": len(rows),
        "trial_count": trial_count,
        "best_strategy": best_strategy,
        "best_sharpe": round(best_sharpe, 6),
        "adjusted_sharpe": round(adjusted_sharpe, 6),
        "selection_haircut": round(selection_haircut, 6),
        "threshold": threshold,
        "passed": passed,
    }


def _compute_pbo_proxy(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    min_trials: int,
    threshold: float | None,
    in_sample_metric: str,
    out_of_sample_metric: str,
) -> dict[str, Any]:
    rows: list[tuple[str, float, float]] = []
    for strategy, metrics in metrics_by_strategy.items():
        in_sample = _to_float(metrics.get(in_sample_metric))
        out_of_sample = _to_float(metrics.get(out_of_sample_metric))
        if in_sample is not None and out_of_sample is not None:
            rows.append((str(strategy), in_sample, out_of_sample))

    rows.sort(key=lambda item: item[1], reverse=True)
    if len(rows) < max(int(min_trials or 0), 1):
        return {
            "available": False,
            "in_sample_metric": in_sample_metric,
            "out_of_sample_metric": out_of_sample_metric,
            "sample_count": len(rows),
            "threshold": threshold,
            "passed": True,
        }

    top_count = max(len(rows) // 2, 1)
    top_rows = rows[:top_count]
    out_of_sample_median = float(median([out_sample for _, _, out_sample in rows]))
    failed_top_count = sum(
        1
        for _, _, out_sample in top_rows
        if out_sample <= out_of_sample_median or out_sample <= 0
    )
    pbo_probability = failed_top_count / len(top_rows)
    passed = True if threshold is None else pbo_probability <= float(threshold)

    return {
        "available": True,
        "in_sample_metric": in_sample_metric,
        "out_of_sample_metric": out_of_sample_metric,
        "sample_count": len(rows),
        "top_in_sample_strategies": [strategy for strategy, _, _ in top_rows],
        "out_of_sample_median": round(out_of_sample_median, 6),
        "pbo_probability": round(pbo_probability, 6),
        "threshold": threshold,
        "passed": passed,
    }


def _compute_deflated_sharpe(
    metrics_by_strategy: Mapping[str, Mapping[str, Any]],
    *,
    trial_count: int,
    min_trials: int,
    threshold: float | None,
    metric: str,
    sample_size_metric: str,
    skew_metric: str,
    kurtosis_metric: str,
) -> dict[str, Any]:
    """Formal Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    Unlike ``_compute_deflated_sharpe_proxy`` (a sqrt(2 ln N) dispersion haircut),
    this deflates the best Sharpe against the *expected maximum* Sharpe under the
    null of zero skill across ``N`` trials, then maps it to a probability via the
    Probabilistic Sharpe Ratio. Returns are assumed normal (skew=0, kurtosis=3)
    unless ``skew_metric`` / ``kurtosis_metric`` are supplied per strategy.
    """
    rows: list[tuple[str, float, Mapping[str, Any]]] = []
    for strategy, metrics in metrics_by_strategy.items():
        value = _to_float(metrics.get(metric))
        if value is not None:
            rows.append((str(strategy), value, metrics))

    rows.sort(key=lambda item: item[1], reverse=True)
    unavailable = {
        "available": False,
        "metric": metric,
        "sample_count": len(rows),
        "trial_count": trial_count,
        "threshold": threshold,
        "passed": True,
    }
    if len(rows) < max(int(min_trials or 0), 1):
        return unavailable

    best_strategy, best_sharpe, best_metrics = rows[0]
    sharpe_values = [value for _, value, _ in rows]
    n_trials = len(sharpe_values)
    if n_trials < 2:
        return unavailable

    sample_size = _to_float(best_metrics.get(sample_size_metric))
    if sample_size is None or sample_size < 2:
        return unavailable

    # Variance of the trial Sharpe estimates (selection dispersion).
    sharpe_variance = variance(sharpe_values)

    # Expected maximum Sharpe under the null of zero true skill across N trials.
    euler_mascheroni = 0.5772156649015329
    normal = NormalDist()
    expected_max_sharpe = math.sqrt(sharpe_variance) * (
        (1.0 - euler_mascheroni) * normal.inv_cdf(1.0 - 1.0 / n_trials)
        + euler_mascheroni * normal.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )

    skew = _to_float(best_metrics.get(skew_metric)) or 0.0
    kurtosis = _to_float(best_metrics.get(kurtosis_metric))
    if kurtosis is None:
        kurtosis = 3.0  # non-excess (normal) kurtosis

    # PSR variance radicand; guard against extreme moments driving it non-positive.
    denom_sq = 1.0 - skew * best_sharpe + ((kurtosis - 1.0) / 4.0) * best_sharpe ** 2
    if denom_sq <= 0.0:
        return unavailable

    deflated = normal.cdf(
        (best_sharpe - expected_max_sharpe)
        * math.sqrt(sample_size - 1.0)
        / math.sqrt(denom_sq)
    )
    passed = True if threshold is None else deflated >= float(threshold)

    return {
        "available": True,
        "metric": metric,
        "sample_count": n_trials,
        "trial_count": trial_count,
        "best_strategy": best_strategy,
        "best_sharpe": round(best_sharpe, 6),
        "sample_size": int(sample_size),
        "sharpe_variance": round(sharpe_variance, 6),
        "expected_max_sharpe": round(expected_max_sharpe, 6),
        "skew": round(skew, 6),
        "kurtosis": round(kurtosis, 6),
        "deflated_sharpe_ratio": round(deflated, 6),
        "threshold": threshold,
        "passed": passed,
    }


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
