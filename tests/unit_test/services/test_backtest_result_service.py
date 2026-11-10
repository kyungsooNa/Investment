from unittest.mock import MagicMock

import pytest

from services.backtest_result_service import BacktestResultService


def _run_summary(**overrides):
    summary = {
        "run_id": "period_vbo_20260101_20261231",
        "strategy": "VBO",
        "target_date": "20260101_20261231",
        "record_count": 2,
        "saved_at": "2026-12-31T08:00:00+00:00",
        "metadata": {
            "dates": ["20260101", "20261231"],
            "date_count": 2,
            "initial_cash": 1_000_000,
            "portfolio": {
                "initial_cash": 1_000_000,
                "realized_net_pnl": -1_000,
                "positions": {"005930": {"qty": 1}},
            },
        },
    }
    summary.update(overrides)
    return summary


def test_list_results_calculates_metrics_and_marks_small_sample():
    repo = MagicMock()
    repo.list_runs.return_value = [_run_summary()]
    repo.load_records.return_value = [
        {"status": "SOLD", "net_pnl": -1_000, "net_return": -2.5},
        {"status": "FILLED", "side": "BUY", "net_pnl": None},
    ]

    results = BacktestResultService(repo).list_results(limit=10)

    assert len(results) == 1
    result = results[0]
    assert result["start_date"] == "20260101"
    assert result["end_date"] == "20261231"
    assert result["completed_trade_count"] == 1
    assert result["open_position_count"] == 1
    assert result["realized_net_pnl"] == -1_000
    assert result["realized_return_pct"] == pytest.approx(-0.1)
    assert result["win_rate"] == 0.0
    assert result["avg_net_return"] == -2.5
    assert result["profit_factor"] == 0.0
    assert result["validity"]["status"] == "INSUFFICIENT_SAMPLE"
    assert "30건" in result["validity"]["reason"]


def test_explicitly_valid_result_with_enough_trades_is_valid():
    repo = MagicMock()
    summary = _run_summary()
    summary["metadata"]["validity_status"] = "VALID"
    summary["metadata"]["portfolio"]["realized_net_pnl"] = 1_000
    repo.list_runs.return_value = [summary]
    repo.load_records.return_value = [
        *({"status": "SOLD", "net_pnl": 100, "net_return": 1.0} for _ in range(20)),
        *({"status": "SOLD", "net_pnl": -100, "net_return": -1.0} for _ in range(10)),
    ]

    result = BacktestResultService(repo).list_results()[0]

    assert result["completed_trade_count"] == 30
    assert result["win_rate"] == pytest.approx(66.6666667)
    assert result["profit_factor"] == 2.0
    assert result["validity"]["status"] == "VALID"


def test_audit_run_is_not_presented_as_profitability_backtest():
    repo = MagicMock()
    summary = _run_summary()
    summary["metadata"]["audit_type"] = "newhigh_strategy_coverage"
    repo.list_runs.return_value = [summary]
    repo.load_records.return_value = []

    result = BacktestResultService(repo).list_results()[0]

    assert result["validity"]["status"] == "AUDIT_ONLY"
    assert "수익성" in result["validity"]["reason"]


def test_results_separate_latest_legacy_and_unknown_config_cohorts():
    repo = MagicMock()
    latest = _run_summary(run_id="latest")
    latest["metadata"]["strategy_config_hash"] = "new123"
    latest["metadata"]["git_commit"] = "abcdef123456"
    legacy = _run_summary(run_id="legacy")
    legacy["metadata"]["strategy_config_hash"] = "old456"
    unknown = _run_summary(run_id="unknown")
    repo.list_runs.return_value = [latest, legacy, unknown]
    repo.load_records.return_value = []

    results = BacktestResultService(repo).list_results()

    assert [(row["config_hash"], row["config_cohort"]) for row in results] == [
        ("new123", "LATEST"),
        ("old456", "LEGACY"),
        ("", "UNKNOWN"),
    ]
    assert results[0]["git_commit"] == "abcdef123456"

