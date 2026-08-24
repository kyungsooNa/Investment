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


def test_records_without_strategy_or_sold_status_are_ignored():
    records = [
        {"status": "HOLD", "strategy": "S1", "signal_time": "2026-05-01", "net_pnl": -100},
        {"status": "SOLD", "strategy": "   ", "signal_time": "2026-05-01", "net_pnl": -100},
        {"status": "SOLD", "strategy": None, "signal_time": "2026-05-01", "net_pnl": -100},
        {"strategy": "S1", "signal_time": "2026-05-01", "net_pnl": -100},
    ]

    summary = compute_portfolio_cooldown_summary(records, consecutive_loss_warning_threshold=1)

    assert summary["strategies"] == {}
    assert summary["candidates"] == []


def test_loss_is_decided_by_net_return_when_net_pnl_is_absent():
    records = [
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-01",
         "net_pnl": None, "net_return": -1.5},
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-02",
         "net_pnl": None, "net_return": -0.5},
    ]

    summary = compute_portfolio_cooldown_summary(records, consecutive_loss_warning_threshold=2)

    assert summary["strategies"]["S1"]["current_consecutive_losses"] == 2


def test_record_without_any_pnl_field_is_not_counted_as_loss():
    records = [
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-01",
         "net_pnl": None, "net_return": None},
    ]

    summary = compute_portfolio_cooldown_summary(records, consecutive_loss_warning_threshold=1)

    assert summary["strategies"]["S1"]["total_loss_count"] == 0
    assert summary["candidates"] == []


def test_unparsable_pnl_values_are_treated_as_missing():
    records = [
        {"status": "SOLD", "strategy": "S1", "signal_time": "2026-05-01",
         "net_pnl": "손실", "net_return": "숫자아님"},
    ]

    summary = compute_portfolio_cooldown_summary(records, consecutive_loss_warning_threshold=1)

    assert summary["strategies"]["S1"]["total_loss_count"] == 0
    assert summary["candidates"] == []


def test_streak_resets_after_a_win_and_records_the_latest_loss_date():
    records = [
        _sold("S1", "2026-05-01", -100, -1.0),
        _sold("S1", "2026-05-02", 50, 0.5),
        _sold("S1", "20260503", -30, -0.3),
    ]

    payload = compute_portfolio_cooldown_summary(records)["strategies"]["S1"]

    assert payload["max_consecutive_losses"] == 1
    assert payload["current_consecutive_losses"] == 1
    assert payload["total_loss_count"] == 2
    assert payload["latest_loss_date"] == "2026-05-03"


def test_closed_time_key_prefers_metadata_over_top_level_fields():
    records = [
        {"status": "SOLD", "strategy": "S1", "net_pnl": -1,
         "metadata": {"sell_date": "2026-05-09"}, "signal_time": "2026-05-01"},
        {"status": "SOLD", "strategy": "S1", "net_pnl": -1,
         "metadata": {"exit_time": ""}, "closed_at": "2026-05-02"},
    ]

    payload = compute_portfolio_cooldown_summary(records)["strategies"]["S1"]

    # metadata.sell_date(05-09) 가 더 뒤라 마지막 손실 일자가 된다.
    assert payload["latest_loss_date"] == "2026-05-09"


def test_threshold_is_clamped_to_at_least_one():
    summary = compute_portfolio_cooldown_summary([], consecutive_loss_warning_threshold=0)

    assert summary["consecutive_loss_warning_threshold"] == 1
    assert summary["strategy_count"] == 0
    assert summary["warnings"] == []
