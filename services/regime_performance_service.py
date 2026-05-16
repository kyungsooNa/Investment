"""시장 국면별 성과 분해.

순수 함수 모듈 — 외부 서비스 의존성 없이 journal records 입력만 받아
KOSPI Bull / KOSDAQ Bull / 지수 횡보 / 지수 하락 / 거래대금 급증 5개 버킷으로 집계.

거래대금 급증 버킷은 market-wide aggregate contract 가 미준비되어
1차 구현에서는 정의만 두고 항상 빈 결과를 반환한다.
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


def _classify_bucket(regime: Mapping[str, Any]) -> str | None:
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


def compute_performance_by_regime(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Records 를 5개 regime 버킷으로 분류해 성과 통계를 계산한다.

    SOLD 상태인 record 만 집계한다 (HOLD/REJECTED/SIGNAL 등은 제외).
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {k: [] for k in BUCKET_KEYS}

    for rec in records:
        if str(rec.get("status") or "").upper() != "SOLD":
            continue
        regime = rec.get("market_regime")
        if not isinstance(regime, Mapping):
            continue
        bucket = _classify_bucket(regime)
        if bucket is not None:
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
