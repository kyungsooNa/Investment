"""시장 국면별 성과 분해.

순수 함수 모듈 — 외부 서비스 의존성 없이 journal records 입력만 받아
KOSPI Bull / KOSDAQ Bull / 지수 횡보 / 지수 하락 / 거래대금 급증 5개 버킷으로 집계.

버킷 분류 모델:
  - KOSPI_BULL / KOSDAQ_BULL / SIDEWAYS / BEAR 는 index 추세 기준 mutually-exclusive 1차 분류
  - TRADING_VALUE_SURGE 는 cross-cutting overlay — 같은 record 가 index 버킷과 surge 버킷에 동시에 집계될 수 있다.
    "KOSPI 상승장 중 거래대금 급증 구간 성과 vs 일반 상승장 성과" 비교가 목적이다.

`market_regime` metadata 입력 contract:
  - kospi: "bull" | "bear" | "sideways" | None
  - kosdaq: "bull" | "bear" | "sideways" | None
  - stock_market: "KOSPI" | "KOSDAQ"
  - trading_value_surge: bool | None — Optional. True 면 record 가 TRADING_VALUE_SURGE 버킷에 추가 집계된다.
    Producer 는 `is_trading_value_surge(current, baseline)` helper 로 일관 산출한다.
    backward-compat: 키 누락 또는 None 은 False 로 취급되어 surge 버킷에 들어가지 않는다.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


BUCKET_KEYS = (
    "KOSPI_BULL",
    "KOSDAQ_BULL",
    "SIDEWAYS",
    "BEAR",
    "TRADING_VALUE_SURGE",
)


DEFAULT_TRADING_VALUE_SURGE_THRESHOLD_PCT = 30.0
"""거래대금 급증 판정 기본 임계값 (baseline 대비 +30% 이상)."""


def is_trading_value_surge(
    current_trading_value: float | None,
    baseline_trading_value: float | None,
    *,
    threshold_pct: float = DEFAULT_TRADING_VALUE_SURGE_THRESHOLD_PCT,
) -> bool:
    """Producer-side helper: 현재 시장 거래대금이 baseline 대비 threshold_pct 이상 초과하면 True.

    baseline 후보: KOSPI/KOSDAQ 시장 거래대금의 N일 이동평균 (예: 5일 MA).
    baseline 이 0 이하 / None 또는 current 가 None 이면 판정 불가 → 보수적으로 False.
    """
    if current_trading_value is None or baseline_trading_value is None:
        return False
    try:
        current = float(current_trading_value)
        baseline = float(baseline_trading_value)
    except (TypeError, ValueError):
        return False
    if baseline <= 0:
        return False
    surge_pct = (current - baseline) / baseline * 100
    return surge_pct >= threshold_pct


def _empty_bucket() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "win_rate": 0.0,
        "avg_net_return": 0.0,
        "total_net_pnl": 0.0,
        "mdd": 0.0,
        "volatility_sample_count": 0,
        "avg_volatility_20d_annualized": None,
        "median_volatility_20d_annualized": None,
    }


def _volatility_stats(records: Iterable[Mapping[str, Any]]) -> tuple[int, float | None, float | None]:
    values: list[float] = []
    for rec in records:
        raw = rec.get("volatility_20d_annualized")
        if raw is None:
            metadata = rec.get("metadata")
            if isinstance(metadata, Mapping):
                raw = metadata.get("volatility_20d_annualized")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        values.append(value)
    if not values:
        return 0, None, None
    avg = sum(values) / len(values)
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2
    return len(values), avg, median


def _classify_primary_bucket(regime: Mapping[str, Any]) -> str | None:
    """Index 추세 기준 mutually-exclusive 1차 버킷. trading_value_surge 와 무관."""
    kospi = str(regime.get("kospi") or "").lower()
    kosdaq = str(regime.get("kosdaq") or "").lower()
    stock_market = str(regime.get("stock_market") or "").upper()

    if "bear" in (kospi, kosdaq):
        return "BEAR"
    if kospi == "sideways" and kosdaq == "sideways":
        return "SIDEWAYS"
    if stock_market == "KOSPI" and kospi == "bull":
        return "KOSPI_BULL"
    if stock_market == "KOSDAQ" and kosdaq == "bull":
        return "KOSDAQ_BULL"
    return None


def _classify_buckets(regime: Mapping[str, Any]) -> list[str]:
    """Record 가 속하는 버킷 목록을 반환한다 (1~2개).

    TRADING_VALUE_SURGE 는 cross-cutting overlay 이므로 index 1차 버킷과 동시에
    포함될 수 있다. 즉 KOSPI 상승장 + 거래대금 급증 record 는 KOSPI_BULL 과
    TRADING_VALUE_SURGE 양쪽 집계에 모두 포함된다.
    """
    buckets: list[str] = []
    primary = _classify_primary_bucket(regime)
    if primary is not None:
        buckets.append(primary)
    if regime.get("trading_value_surge") is True:
        buckets.append("TRADING_VALUE_SURGE")
    return buckets


def compute_performance_by_regime(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Records 를 5개 regime 버킷으로 분류해 성과 통계를 계산한다.

    SOLD 상태인 record 만 집계한다 (HOLD/REJECTED/SIGNAL 등은 제외).
    TRADING_VALUE_SURGE 는 overlay 이므로 같은 record 가 index 버킷과 동시 집계될 수 있다 —
    따라서 모든 버킷의 `trade_count` 합이 입력 record 수보다 클 수 있다.
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {k: [] for k in BUCKET_KEYS}

    for rec in records:
        if str(rec.get("status") or "").upper() != "SOLD":
            continue
        regime = rec.get("market_regime")
        if not isinstance(regime, Mapping):
            continue
        for bucket in _classify_buckets(regime):
            buckets[bucket].append(rec)

    result: dict[str, dict[str, Any]] = {k: _empty_bucket() for k in BUCKET_KEYS}
    for bucket_name, trades in buckets.items():
        if not trades:
            continue
        ordered = sorted(trades, key=lambda r: str(r.get("signal_time") or ""))
        net_pnls = [float(r.get("net_pnl") or 0) for r in ordered]
        net_returns = [float(r.get("net_return") or 0) for r in ordered]
        win_count = sum(1 for v in net_pnls if v > 0)
        total_pnl = sum(net_pnls)

        # MDD: 누적 net_pnl 의 peak-to-trough
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

        vol_count, vol_avg, vol_median = _volatility_stats(ordered)

        result[bucket_name] = {
            "trade_count": len(trades),
            "win_count": win_count,
            "win_rate": win_count / len(trades),
            "avg_net_return": sum(net_returns) / len(net_returns),
            "total_net_pnl": total_pnl,
            "mdd": mdd,
            "volatility_sample_count": vol_count,
            "avg_volatility_20d_annualized": vol_avg,
            "median_volatility_20d_annualized": vol_median,
        }

    return result


def compute_regime_balance_summary(
    regime_performance: Mapping[str, Mapping[str, Any]],
    *,
    required_buckets: Iterable[str],
    min_trades_per_bucket: int,
) -> dict[str, Any]:
    """Report whether validation covers required market-regime buckets."""
    required = [str(bucket).strip().upper() for bucket in required_buckets if str(bucket).strip()]
    min_trades = max(int(min_trades_per_bucket or 0), 1)
    missing: list[str] = []
    weak: list[dict[str, Any]] = []

    for bucket in required:
        metrics = regime_performance.get(bucket) or {}
        trade_count = int(metrics.get("trade_count") or 0)
        if trade_count <= 0:
            missing.append(bucket)
        elif trade_count < min_trades:
            weak.append(
                {
                    "bucket": bucket,
                    "trade_count": trade_count,
                    "required": min_trades,
                }
            )

    return {
        "required_regimes": required,
        "min_trades_per_regime": min_trades,
        "missing_regimes": missing,
        "weak_regimes": weak,
        "balanced_pass": not missing and not weak,
    }
