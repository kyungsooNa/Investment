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


def test_zero_variance_pair_is_reported_as_skipped():
    """한쪽 전략의 일별 손익이 완전히 평탄하면 상관계수를 낼 수 없다."""
    records = [
        _sold("S1", "2026-05-01", 1.0),
        _sold("S1", "2026-05-02", 1.0),
        _sold("S1", "2026-05-03", 1.0),
        _sold("S2", "2026-05-01", 1.0),
        _sold("S2", "2026-05-02", -2.0),
        _sold("S2", "2026-05-03", 3.0),
    ]

    summary = compute_strategy_correlation_summary(records, min_overlap=3)

    assert summary["pairs"] == []
    assert summary["skipped_pairs"][0]["reason"] == "zero_variance"


def test_records_without_strategy_or_date_are_dropped():
    records = [
        {"status": "SOLD", "strategy": "", "signal_time": "2026-05-01", "net_return": 1.0},
        {"status": "SOLD", "strategy": "S1", "signal_time": "", "net_return": 1.0},
    ]

    summary = compute_strategy_correlation_summary(records, min_overlap=1)

    assert summary["strategy_count"] == 0


def test_records_without_any_usable_metric_are_dropped():
    records = [
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-01",
         "net_return": None, "net_pnl": None},
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-02",
         "net_return": "숫자아님", "net_pnl": "숫자아님"},
    ]

    summary = compute_strategy_correlation_summary(records, min_overlap=1)

    assert summary["strategy_count"] == 0


def test_metric_falls_back_to_net_pnl_when_the_requested_one_is_missing():
    records = [
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-01",
         "net_return": None, "net_pnl": 100},
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-02",
         "net_return": None, "net_pnl": -50},
        {"status": "SOLD", "strategy": "S2", "signal_time": "2026-05-01",
         "net_return": None, "net_pnl": 200},
        {"status": "SOLD", "strategy": "S2", "signal_time": "2026-05-02",
         "net_return": None, "net_pnl": -100},
    ]

    summary = compute_strategy_correlation_summary(
        records, metric="net_return", min_overlap=2
    )

    assert summary["pairs"][0]["correlation"] == 1.0


def test_pearson_requires_two_aligned_points():
    from services.strategy_correlation_service import _pearson

    assert _pearson([1.0], [2.0]) is None
    assert _pearson([1.0, 2.0], [1.0]) is None
    assert _pearson([1.0, 2.0], [1.0, 2.0]) == 1.0


def test_record_date_normalizes_compact_dates_and_falls_back_to_raw():
    from services.strategy_correlation_service import _record_date

    assert _record_date({"signal_time": "2026-05-01 09:30"}) == "2026-05-01"
    assert _record_date({"date": "20260501"}) == "2026-05-01"
    assert _record_date({"date": "2026-05-01T09:30"}) == "2026-05-01"
    assert _record_date({"date": "봄철"}) == "봄철"
    assert _record_date({}) == ""


@pytest.mark.parametrize(
    "raw, expected", [(None, None), ("", None), ("1.5", 1.5), (2, 2.0), ("숫자아님", None)]
)
def test_float_coercion(raw, expected):
    from services.strategy_correlation_service import _to_float

    assert _to_float(raw) == expected
