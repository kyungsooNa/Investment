import pytest

from services.market_beta_service import compute_market_beta_summary


def _sold(strategy: str, signal_time: str, net_return: float, market_return: float) -> dict:
    return {
        "status": "SOLD",
        "strategy": strategy,
        "signal_time": signal_time,
        "net_return": net_return,
        "market_return": market_return,
    }


def test_compute_market_beta_summary_reports_high_portfolio_beta():
    records = [
        _sold("S1", "2026-05-01", 2.0, 1.0),
        _sold("S1", "2026-05-02", 4.0, 2.0),
        _sold("S1", "2026-05-03", 6.0, 3.0),
    ]

    summary = compute_market_beta_summary(
        records,
        min_overlap=3,
        warning_threshold=1.5,
    )

    assert summary["portfolio"] == {
        "status": "ok",
        "beta": pytest.approx(2.0),
        "overlap": 3,
    }
    assert summary["strategies"]["S1"] == {
        "status": "ok",
        "beta": pytest.approx(2.0),
        "overlap": 3,
    }
    assert summary["high_beta_strategies"] == [
        {"strategy": "S1", "beta": pytest.approx(2.0), "overlap": 3}
    ]
    assert summary["warnings"] == [
        "portfolio_market_beta_high",
        "strategy_market_beta_high",
    ]


def test_compute_market_beta_summary_supports_metadata_benchmark_return():
    records = [
        {
            "status": "SOLD",
            "strategy": "S1",
            "date": "20260501",
            "net_return": 1.5,
            "metadata": {"benchmark_return": 1.0},
        },
        {
            "status": "SOLD",
            "strategy": "S1",
            "date": "20260502",
            "net_return": 3.0,
            "metadata": {"benchmark_return": 2.0},
        },
    ]

    summary = compute_market_beta_summary(records, min_overlap=2, warning_threshold=2.0)

    assert summary["portfolio"]["beta"] == pytest.approx(1.5)
    assert summary["warnings"] == []


def test_compute_market_beta_summary_marks_insufficient_or_zero_variance():
    insufficient = compute_market_beta_summary(
        [_sold("S1", "2026-05-01", 1.0, 1.0)],
        min_overlap=2,
    )
    zero_variance = compute_market_beta_summary(
        [
            _sold("S1", "2026-05-01", 1.0, 1.0),
            _sold("S1", "2026-05-02", 2.0, 1.0),
        ],
        min_overlap=2,
    )

    assert insufficient["portfolio"] == {
        "status": "insufficient_sample",
        "beta": None,
        "overlap": 1,
    }
    assert zero_variance["portfolio"] == {
        "status": "zero_benchmark_variance",
        "beta": None,
        "overlap": 2,
    }
    assert insufficient["warnings"] == []
    assert zero_variance["warnings"] == []
