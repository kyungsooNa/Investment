"""regime_performance_service.compute_performance_by_regime 단위 테스트.

순수 함수 모듈 — 외부 서비스 의존성 없이 입력 records 만 받는다.

버킷:
  1. KOSPI Bull          (stock_market == "KOSPI"  && kospi == "bull")
  2. KOSDAQ Bull         (stock_market == "KOSDAQ" && kosdaq == "bull")
  3. 지수 횡보(sideways) (kospi == "sideways" && kosdaq == "sideways")
  4. 지수 하락(bear)     (kospi == "bear" || kosdaq == "bear")
  5. 거래대금 급증       (trading_value_surge == True)  ← cross-cutting overlay
"""
import pytest

from services.regime_performance_service import (
    DEFAULT_TRADING_VALUE_SURGE_THRESHOLD_PCT,
    compute_performance_by_regime,
    compute_regime_balance_summary,
    is_trading_value_surge,
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


def test_trading_value_surge_overlay_record_appears_in_both_buckets():
    """surge=True 인 KOSPI bull record 는 KOSPI_BULL 과 TRADING_VALUE_SURGE 양쪽에 집계된다."""
    records = [
        _trade(
            net_pnl=100.0, net_return=1.0, signal_time="20260514",
            stock_market="KOSPI", kospi="bull", kosdaq="sideways", surge=True,
        ),
    ]
    res = compute_performance_by_regime(records)

    assert res["KOSPI_BULL"]["trade_count"] == 1
    assert res["TRADING_VALUE_SURGE"]["trade_count"] == 1
    assert res["TRADING_VALUE_SURGE"]["total_net_pnl"] == 100.0


def test_trading_value_surge_overlay_skipped_when_flag_false():
    """surge=False (default) 인 record 는 TRADING_VALUE_SURGE 버킷에 들어가지 않는다."""
    records = [
        _trade(net_pnl=100.0, net_return=1.0, signal_time="20260514", surge=False),
    ]
    res = compute_performance_by_regime(records)

    assert res["KOSPI_BULL"]["trade_count"] == 1
    assert res["TRADING_VALUE_SURGE"]["trade_count"] == 0


def test_trading_value_surge_overlay_without_index_classification():
    """index 정보가 없어도 surge=True 면 TRADING_VALUE_SURGE 버킷에 단독 집계된다."""
    records = [
        _trade(
            net_pnl=50.0, net_return=0.5, signal_time="20260514",
            stock_market="UNKNOWN", kospi="", kosdaq="", surge=True,
        ),
    ]
    res = compute_performance_by_regime(records)

    assert res["TRADING_VALUE_SURGE"]["trade_count"] == 1
    assert res["KOSPI_BULL"]["trade_count"] == 0
    assert res["KOSDAQ_BULL"]["trade_count"] == 0


def test_trading_value_surge_backward_compat_missing_key():
    """legacy record (market_regime 에 trading_value_surge 키 누락) 는 surge 버킷 미집계."""
    rec = {
        "status": "SOLD",
        "net_pnl": 100.0,
        "net_return": 1.0,
        "signal_time": "20260514",
        "market_regime": {
            "kospi": "bull",
            "kosdaq": "sideways",
            "stock_market": "KOSPI",
            # trading_value_surge 키 없음
        },
    }
    res = compute_performance_by_regime([rec])

    assert res["KOSPI_BULL"]["trade_count"] == 1
    assert res["TRADING_VALUE_SURGE"]["trade_count"] == 0


# === is_trading_value_surge helper ===


def test_is_trading_value_surge_above_threshold_returns_true():
    """current 가 baseline 대비 default threshold(+30%) 이상 초과 → True."""
    assert is_trading_value_surge(current_trading_value=1300, baseline_trading_value=1000) is True


def test_is_trading_value_surge_below_threshold_returns_false():
    """baseline 대비 30% 미만 초과는 surge 아님."""
    assert is_trading_value_surge(current_trading_value=1299, baseline_trading_value=1000) is False


def test_is_trading_value_surge_exact_threshold_returns_true():
    """정확히 threshold (+30%) 도달 시 True (>= 비교)."""
    assert is_trading_value_surge(current_trading_value=1300.0, baseline_trading_value=1000.0) is True


def test_is_trading_value_surge_custom_threshold():
    """threshold_pct 인자로 임계값 변경 가능."""
    # baseline 1000, current 1500 → +50%
    assert is_trading_value_surge(1500, 1000, threshold_pct=50.0) is True
    assert is_trading_value_surge(1500, 1000, threshold_pct=60.0) is False


def test_is_trading_value_surge_none_inputs_return_false():
    """current 또는 baseline 이 None 이면 보수적으로 False."""
    assert is_trading_value_surge(None, 1000) is False
    assert is_trading_value_surge(1300, None) is False
    assert is_trading_value_surge(None, None) is False


def test_is_trading_value_surge_zero_or_negative_baseline_returns_false():
    """baseline 이 0 이하면 비율 계산 불가 → False."""
    assert is_trading_value_surge(1300, 0) is False
    assert is_trading_value_surge(1300, -100) is False


def test_is_trading_value_surge_invalid_input_returns_false():
    """숫자로 변환 불가능한 입력은 보수적으로 False."""
    assert is_trading_value_surge("not_a_number", 1000) is False  # type: ignore[arg-type]
    assert is_trading_value_surge(1300, "not_a_number") is False  # type: ignore[arg-type]


def test_default_threshold_constant_is_30_pct():
    """default threshold 상수는 30%."""
    assert DEFAULT_TRADING_VALUE_SURGE_THRESHOLD_PCT == 30.0


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
