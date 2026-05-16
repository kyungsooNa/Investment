"""regime_performance_service.compute_performance_by_regime 변동성 집계 테스트.

각 버킷에 volatility_sample_count, avg_volatility_20d_annualized,
median_volatility_20d_annualized 필드가 추가되었음을 검증한다.
record.volatility_20d_annualized 와 record.metadata.volatility_20d_annualized
양쪽 모두 fallback 으로 사용된다.
"""
import pytest

from services.regime_performance_service import compute_performance_by_regime


def _trade(net_pnl, signal_time, *, vol=None, vol_in_metadata=False, kospi="bull", kosdaq="bull",
           stock_market="KOSPI", status="SOLD", net_return=0.0):
    rec = {
        "status": status,
        "net_pnl": net_pnl,
        "net_return": net_return,
        "signal_time": signal_time,
        "market_regime": {
            "kospi": kospi,
            "kosdaq": kosdaq,
            "stock_market": stock_market,
            "trading_value_surge": False,
        },
    }
    if vol is not None:
        if vol_in_metadata:
            rec["metadata"] = {"volatility_20d_annualized": vol}
        else:
            rec["volatility_20d_annualized"] = vol
    return rec


def test_empty_bucket_has_volatility_keys_with_none_default():
    res = compute_performance_by_regime([])
    bull = res["KOSPI_BULL"]
    assert bull["volatility_sample_count"] == 0
    assert bull["avg_volatility_20d_annualized"] is None
    assert bull["median_volatility_20d_annualized"] is None


def test_avg_and_median_volatility_in_bucket():
    records = [
        _trade(100, "2026-01-01", vol=0.10),
        _trade(200, "2026-01-02", vol=0.30),
        _trade(50, "2026-01-03", vol=0.20),
    ]
    res = compute_performance_by_regime(records)
    bull = res["KOSPI_BULL"]
    assert bull["volatility_sample_count"] == 3
    assert bull["avg_volatility_20d_annualized"] == pytest.approx(0.20)
    assert bull["median_volatility_20d_annualized"] == pytest.approx(0.20)


def test_median_even_count_uses_two_middle_average():
    records = [
        _trade(10, "2026-01-01", vol=0.10),
        _trade(10, "2026-01-02", vol=0.40),
    ]
    res = compute_performance_by_regime(records)
    bull = res["KOSPI_BULL"]
    assert bull["median_volatility_20d_annualized"] == pytest.approx(0.25)


def test_volatility_falls_back_to_metadata_field():
    records = [
        _trade(10, "2026-01-01", vol=0.20, vol_in_metadata=True),
    ]
    res = compute_performance_by_regime(records)
    bull = res["KOSPI_BULL"]
    assert bull["volatility_sample_count"] == 1
    assert bull["avg_volatility_20d_annualized"] == pytest.approx(0.20)


def test_invalid_volatility_excluded_from_stats():
    records = [
        _trade(10, "2026-01-01", vol="bad"),
        _trade(10, "2026-01-02", vol=0.15),
    ]
    res = compute_performance_by_regime(records)
    bull = res["KOSPI_BULL"]
    assert bull["volatility_sample_count"] == 1
    assert bull["avg_volatility_20d_annualized"] == pytest.approx(0.15)
