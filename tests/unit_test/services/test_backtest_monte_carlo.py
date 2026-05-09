from __future__ import annotations

from services.backtest_monte_carlo import (
    BacktestMonteCarloConfig,
    BacktestMonteCarloSimulator,
    calculate_trade_path_metrics,
    extract_net_pnls_from_journal,
)


def test_extract_net_pnls_from_journal_uses_completed_trade_net_pnl_only():
    records = [
        {"status": "SOLD", "net_pnl": "1000"},
        {"status": "FILLED", "net_pnl": None},
        {"status": "REJECTED", "net_pnl": -999},
        {"status": "ROUND_TRIP", "net_pnl": -500.5},
        {"status": "SOLD", "net_pnl": "not-a-number"},
    ]

    assert extract_net_pnls_from_journal(records) == [1000.0, -500.5]


def test_calculate_trade_path_metrics_tracks_mdd_and_losing_streak():
    metrics = calculate_trade_path_metrics(
        [100.0, -50.0, -25.0, 200.0, -10.0],
        initial_capital=1_000.0,
    )

    assert metrics.final_equity == 1_215.0
    assert metrics.max_drawdown == 75.0
    assert round(metrics.max_drawdown_pct, 4) == round(75.0 / 1100.0 * 100, 4)
    assert metrics.longest_losing_streak == 2


def test_monte_carlo_simulator_is_deterministic_with_seed():
    config = BacktestMonteCarloConfig(
        runs=10,
        seed=7,
        initial_capital=1_000.0,
        ruin_drawdown_pct=10.0,
    )

    first = BacktestMonteCarloSimulator(config).run([100.0, -50.0, 20.0, -30.0])
    second = BacktestMonteCarloSimulator(config).run([100.0, -50.0, 20.0, -30.0])

    assert first.to_dict() == second.to_dict()
    assert first.trade_count == 4
    assert first.runs == 10


def test_monte_carlo_simulator_reports_ruin_probability():
    result = BacktestMonteCarloSimulator(
        BacktestMonteCarloConfig(
            runs=5,
            seed=1,
            initial_capital=1_000.0,
            ruin_drawdown_pct=1.0,
        )
    ).run([-20.0, -10.0])

    assert result.ruin_probability == 1.0
    assert result.worst_losing_streak == 2
    assert result.worst_max_drawdown >= 30.0


def test_monte_carlo_simulator_handles_no_trades():
    result = BacktestMonteCarloSimulator(
        BacktestMonteCarloConfig(runs=20, seed=1, initial_capital=1_000.0)
    ).run([])

    assert result.trade_count == 0
    assert result.runs == 0
    assert result.avg_final_equity == 1_000.0
    assert result.ruin_probability == 0.0
