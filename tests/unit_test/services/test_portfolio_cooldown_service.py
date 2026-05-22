from services.portfolio_cooldown_service import compute_portfolio_cooldown_summary


def _sold(strategy: str, signal_time: str, net_pnl: float, net_return: float | None = None) -> dict:
    return {
        "status": "SOLD",
        "strategy": strategy,
        "signal_time": signal_time,
        "net_pnl": net_pnl,
        "net_return": net_return,
    }


def test_compute_portfolio_cooldown_summary_reports_consecutive_loss_candidate():
    records = [
        _sold("S1", "2026-05-01", -100, -1.0),
        _sold("S1", "2026-05-02", -50, -0.5),
        _sold("S1", "2026-05-03", -30, -0.3),
        _sold("S2", "2026-05-01", -10, -0.1),
        _sold("S2", "2026-05-02", 20, 0.2),
    ]

    summary = compute_portfolio_cooldown_summary(
        records,
        consecutive_loss_warning_threshold=3,
    )

    assert summary["strategy_count"] == 2
    assert summary["warnings"] == ["portfolio_consecutive_loss_cooldown_candidate"]
    assert summary["candidates"] == [
        {
            "strategy": "S1",
            "max_consecutive_losses": 3,
            "current_consecutive_losses": 3,
            "latest_loss_date": "2026-05-03",
            "total_loss_count": 3,
        }
    ]
    assert summary["strategies"]["S1"]["max_consecutive_losses"] == 3


def test_compute_portfolio_cooldown_summary_resets_streak_on_win_and_ignores_open_entries():
    records = [
        _sold("S1", "2026-05-01", -100),
        _sold("S1", "2026-05-02", -50),
        _sold("S1", "2026-05-03", 50),
        _sold("S1", "2026-05-04", -30),
        {"status": "FILLED", "side": "BUY", "strategy": "S1", "signal_time": "2026-05-05"},
    ]

    summary = compute_portfolio_cooldown_summary(
        records,
        consecutive_loss_warning_threshold=2,
    )

    assert summary["warnings"] == []
    assert summary["candidates"] == []
    assert summary["strategies"]["S1"]["max_consecutive_losses"] == 2
    assert summary["strategies"]["S1"]["current_consecutive_losses"] == 1
