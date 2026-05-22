"""regime_performance_service.compute_performance_by_regime 단위 테스트.

순수 함수 모듈 — 외부 서비스 의존성 없이 입력 records 만 받는다.

버킷:
  1. KOSPI Bull          (stock_market == "KOSPI"  && kospi == "bull")
  2. KOSDAQ Bull         (stock_market == "KOSDAQ" && kosdaq == "bull")
  3. 지수 횡보(sideways) (kospi == "sideways" && kosdaq == "sideways")
  4. 지수 하락(bear)     (kospi == "bear" || kosdaq == "bear")
  5. 거래대금 급증       (trading_value_surge == True)  ← 1차 구현은 빈 결과 (overlay)
"""
import pytest

from services.regime_performance_service import (
    compute_performance_by_regime,
    compute_regime_balance_summary,
)


def _trade(net_pnl, net_return, signal_time, kospi="bull", kosdaq="bull",
           stock_market="KOSPI", surge=False, status="SOLD"):
    return {
        "status": status,
        "net_pnl": net_pnl,
        "net_return": net_return,
        "signal_time": signal_time,
        "market_regime": {
            "kospi": kospi,
            "kosdaq": kosdaq,
            "stock_market": stock_market,
            "trading_value_surge": surge,
        },
    }


def test_empty_records_returns_zeroed_buckets():
    res = compute_performance_by_regime([])
    assert set(res.keys()) >= {"KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR", "TRADING_VALUE_SURGE"}
    for bucket in res.values():
        assert bucket["trade_count"] == 0
        assert bucket["win_rate"] == 0.0
        assert bucket["total_net_pnl"] == 0.0


def test_kospi_bull_bucket_aggregates():
    records = [
        _trade(net_pnl=1000.0, net_return=2.0, signal_time="20260514", stock_market="KOSPI"),
        _trade(net_pnl=-500.0, net_return=-1.0, signal_time="20260515", stock_market="KOSPI"),
    ]
    res = compute_performance_by_regime(records)
    bull = res["KOSPI_BULL"]
    assert bull["trade_count"] == 2
    assert bull["total_net_pnl"] == 500.0
    assert bull["win_rate"] == pytest.approx(0.5)
    assert bull["avg_net_return"] == pytest.approx(0.5)


def test_kosdaq_bull_bucket_separate_from_kospi():
    records = [
        _trade(net_pnl=200.0, net_return=1.0, signal_time="20260514",
               stock_market="KOSDAQ", kospi="bull", kosdaq="bull"),
    ]
    res = compute_performance_by_regime(records)
    assert res["KOSDAQ_BULL"]["trade_count"] == 1
    assert res["KOSPI_BULL"]["trade_count"] == 0


def test_sideways_bucket():
    records = [
        _trade(net_pnl=100.0, net_return=1.0, signal_time="20260514",
               kospi="sideways", kosdaq="sideways"),
    ]
    res = compute_performance_by_regime(records)
    assert res["SIDEWAYS"]["trade_count"] == 1


def test_bear_bucket_triggers_on_either_market():
    records = [
        _trade(net_pnl=-100.0, net_return=-1.0, signal_time="20260514",
               kospi="bear", kosdaq="bull"),
        _trade(net_pnl=-200.0, net_return=-2.0, signal_time="20260515",
               kospi="bull", kosdaq="bear"),
    ]
    res = compute_performance_by_regime(records)
    assert res["BEAR"]["trade_count"] == 2


def test_trading_value_surge_bucket_is_overlay():
    """급증 장세는 1차 구현에서는 빈 결과 (오버레이 정의만 유지)."""
    records = [
        _trade(net_pnl=100.0, net_return=1.0, signal_time="20260514", surge=True),
    ]
    res = compute_performance_by_regime(records)
    # 1차 구현: overlay 미집계 — 0 으로 유지
    assert res["TRADING_VALUE_SURGE"]["trade_count"] == 0


def test_mdd_computed_from_signal_time_ordered_cumulative_pnl():
    """MDD = peak-to-trough on cumulative net_pnl, sorted by signal_time."""
    records = [
        _trade(net_pnl=1000.0, net_return=2.0, signal_time="20260514"),
        _trade(net_pnl=-2000.0, net_return=-4.0, signal_time="20260515"),
        _trade(net_pnl=500.0, net_return=1.0, signal_time="20260516"),
    ]
    res = compute_performance_by_regime(records)
    # 누적: 1000, -1000, -500 — peak=1000, trough=-1000 → MDD = 2000
    assert res["KOSPI_BULL"]["mdd"] == pytest.approx(2000.0)


def test_records_without_market_regime_are_skipped():
    """market_regime 누락 record 는 어느 버킷에도 들어가지 않는다."""
    records = [{"status": "SOLD", "net_pnl": 100.0, "net_return": 1.0, "signal_time": "20260514"}]
    res = compute_performance_by_regime(records)
    for bucket in res.values():
        assert bucket["trade_count"] == 0


def test_non_sold_records_are_excluded():
    """체결 미완료(HOLD/REJECTED/SIGNAL) 는 성과 집계 대상이 아님."""
    records = [
        _trade(net_pnl=1000.0, net_return=2.0, signal_time="20260514", status="HOLD"),
        _trade(net_pnl=1000.0, net_return=2.0, signal_time="20260515", status="SOLD"),
    ]
    res = compute_performance_by_regime(records)
    assert res["KOSPI_BULL"]["trade_count"] == 1


def test_regime_balance_summary_reports_missing_and_weak_buckets():
    regime_performance = compute_performance_by_regime([
        _trade(net_pnl=100.0, net_return=1.0, signal_time="20260514", stock_market="KOSPI"),
        _trade(net_pnl=50.0, net_return=0.5, signal_time="20260515", stock_market="KOSDAQ"),
    ])

    summary = compute_regime_balance_summary(
        regime_performance,
        required_buckets=("KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR"),
        min_trades_per_bucket=2,
    )

    assert summary["balanced_pass"] is False
    assert summary["missing_regimes"] == ["SIDEWAYS", "BEAR"]
    assert summary["weak_regimes"] == [
        {"bucket": "KOSPI_BULL", "trade_count": 1, "required": 2},
        {"bucket": "KOSDAQ_BULL", "trade_count": 1, "required": 2},
    ]


def test_regime_balance_summary_passes_when_required_buckets_have_enough_trades():
    regime_performance = {
        "KOSPI_BULL": {"trade_count": 2},
        "KOSDAQ_BULL": {"trade_count": 2},
        "SIDEWAYS": {"trade_count": 2},
        "BEAR": {"trade_count": 2},
    }

    summary = compute_regime_balance_summary(
        regime_performance,
        required_buckets=("KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR"),
        min_trades_per_bucket=2,
    )

    assert summary["balanced_pass"] is True
    assert summary["missing_regimes"] == []
    assert summary["weak_regimes"] == []
