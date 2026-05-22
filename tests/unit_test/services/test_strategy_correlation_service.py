import pytest

from services.strategy_correlation_service import compute_strategy_correlation_summary


def _sold(strategy: str, signal_time: str, net_return: float) -> dict:
    return {
        "status": "SOLD",
        "strategy": strategy,
        "signal_time": signal_time,
        "net_return": net_return,
        "net_pnl": net_return * 100,
    }


def test_compute_strategy_correlation_summary_reports_high_positive_pair():
    records = [
        _sold("S1", "2026-05-01 09:00:00", 1.0),
        _sold("S1", "2026-05-02 09:00:00", 2.0),
        _sold("S1", "2026-05-03 09:00:00", 3.0),
        _sold("S2", "2026-05-01 09:00:00", 2.0),
        _sold("S2", "2026-05-02 09:00:00", 4.0),
        _sold("S2", "2026-05-03 09:00:00", 6.0),
        _sold("S3", "2026-05-01 09:00:00", 3.0),
        _sold("S3", "2026-05-02 09:00:00", 2.0),
        _sold("S3", "2026-05-03 09:00:00", 1.0),
    ]

    summary = compute_strategy_correlation_summary(
        records,
        min_overlap=3,
        warning_threshold=0.9,
    )

    assert summary["strategy_count"] == 3
    assert summary["pair_count"] == 3
    assert summary["max_positive_pair"] == {
        "left": "S1",
        "right": "S2",
        "correlation": pytest.approx(1.0),
        "overlap": 3,
    }
    assert summary["high_correlation_pairs"] == [
        {
            "left": "S1",
            "right": "S2",
            "correlation": pytest.approx(1.0),
            "overlap": 3,
        }
    ]
    assert summary["warnings"] == ["strategy_correlation_high"]


def test_compute_strategy_correlation_summary_marks_insufficient_overlap():
    records = [
        _sold("S1", "2026-05-01", 1.0),
        _sold("S2", "2026-05-02", 1.0),
    ]

    summary = compute_strategy_correlation_summary(records, min_overlap=2)

    assert summary["pair_count"] == 0
    assert summary["skipped_pairs"] == [
        {"left": "S1", "right": "S2", "reason": "insufficient_overlap", "overlap": 0}
    ]
    assert summary["warnings"] == []
