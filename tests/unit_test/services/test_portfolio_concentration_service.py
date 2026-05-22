import pytest

from services.portfolio_concentration_service import compute_portfolio_concentration_summary


def test_compute_portfolio_concentration_summary_reports_position_and_strategy_exposure():
    positions = {
        "005930": {"total_cost": 300_000, "strategy": "S1"},
        "000660": {"total_cost": 200_000, "strategy": "S1"},
        "035720": {"total_cost": 100_000, "strategy": "S2"},
    }

    summary = compute_portfolio_concentration_summary(
        positions,
        capital_basis=1_000_000,
        warn_total_exposure_pct=50.0,
        warn_position_concentration_pct=25.0,
        warn_strategy_concentration_pct=40.0,
    )

    assert summary["capital_basis"] == 1_000_000
    assert summary["total_exposure_won"] == 600_000
    assert summary["total_exposure_pct"] == pytest.approx(60.0)
    assert summary["max_position"] == {
        "code": "005930",
        "exposure_won": 300_000,
        "exposure_pct": 30.0,
    }
    assert summary["max_strategy"] == {
        "strategy": "S1",
        "exposure_won": 500_000,
        "exposure_pct": 50.0,
    }
    assert summary["warnings"] == [
        "portfolio_total_exposure_high",
        "single_position_concentration_high",
        "strategy_concentration_high",
    ]


def test_compute_portfolio_concentration_summary_handles_empty_or_zero_capital():
    assert compute_portfolio_concentration_summary({}, capital_basis=1_000_000)["warnings"] == []

    summary = compute_portfolio_concentration_summary(
        {"005930": {"total_cost": 100_000, "strategy": "S1"}},
        capital_basis=0,
    )

    assert summary["capital_basis"] == 0
    assert summary["total_exposure_pct"] is None
    assert summary["warnings"] == ["portfolio_concentration_unknown_capital"]


def test_compute_portfolio_concentration_summary_reports_market_exposure():
    positions = {
        "005930": {"total_cost": 300_000, "strategy": "S1", "stock_market": "KOSPI"},
        "000660": {"total_cost": 200_000, "strategy": "S1", "market": "KOSPI"},
        "035720": {"total_cost": 300_000, "strategy": "S2", "stock_market": "KOSDAQ"},
    }

    summary = compute_portfolio_concentration_summary(
        positions,
        capital_basis=1_000_000,
        warn_total_exposure_pct=None,
        warn_position_concentration_pct=None,
        warn_strategy_concentration_pct=None,
        warn_market_concentration_pct=45.0,
    )

    assert summary["market_exposures"]["KOSPI"] == {
        "exposure_won": 500_000,
        "exposure_pct": 50.0,
        "position_count": 2,
    }
    assert summary["market_exposures"]["KOSDAQ"] == {
        "exposure_won": 300_000,
        "exposure_pct": 30.0,
        "position_count": 1,
    }
    assert summary["max_market"] == {
        "market": "KOSPI",
        "exposure_won": 500_000,
        "exposure_pct": 50.0,
        "position_count": 2,
    }
    assert summary["warnings"] == ["market_concentration_high"]


def test_compute_portfolio_concentration_summary_groups_unclassified_market_as_unknown():
    summary = compute_portfolio_concentration_summary(
        {"005930": {"total_cost": 100_000, "strategy": "S1"}},
        capital_basis=1_000_000,
        warn_market_concentration_pct=5.0,
    )

    assert summary["market_exposures"]["UNKNOWN"]["exposure_pct"] == 10.0
    assert summary["max_market"]["market"] == "UNKNOWN"
    assert "market_concentration_high" not in summary["warnings"]
