from services.portfolio_entry_pressure_service import compute_portfolio_entry_pressure_summary


def _entry(strategy: str, signal_time: str, status: str = "FILLED") -> dict:
    return {
        "status": status,
        "side": "BUY",
        "strategy": strategy,
        "signal_time": signal_time,
    }


def test_compute_portfolio_entry_pressure_summary_warns_on_daily_entry_burst():
    records = [
        _entry("S1", "2026-05-01 09:00:00"),
        _entry("S2", "2026-05-01 09:01:00"),
        _entry("S3", "2026-05-01 09:02:00"),
        _entry("S1", "2026-05-02 09:00:00"),
    ]

    summary = compute_portfolio_entry_pressure_summary(
        records,
        daily_entry_warning_threshold=3,
    )

    assert summary["total_entry_count"] == 4
    assert summary["max_daily_entry_count"] == 3
    assert summary["max_daily_entry_date"] == "2026-05-01"
    assert summary["daily_entries"]["2026-05-01"]["entry_count"] == 3
    assert summary["daily_entries"]["2026-05-01"]["strategies"] == ["S1", "S2", "S3"]
    assert summary["intraday_windows"]["opening"]["max_entry_count"] == 3
    assert summary["intraday_windows"]["opening"]["max_entry_date"] == "2026-05-01"
    assert summary["warnings"] == [
        "portfolio_daily_entry_pressure_high",
        "portfolio_opening_entry_pressure_high",
    ]


def test_compute_portfolio_entry_pressure_summary_warns_on_closing_entry_burst():
    records = [
        _entry("S1", "2026-05-01 14:30:00"),
        _entry("S2", "2026-05-01 15:00:00"),
        _entry("S3", "2026-05-02 09:30:00"),
    ]

    summary = compute_portfolio_entry_pressure_summary(
        records,
        daily_entry_warning_threshold=5,
        opening_entry_warning_threshold=3,
        closing_entry_warning_threshold=2,
    )

    assert summary["intraday_windows"]["closing"]["max_entry_count"] == 2
    assert summary["intraday_windows"]["closing"]["max_entry_date"] == "2026-05-01"
    assert summary["warnings"] == ["portfolio_closing_entry_pressure_high"]


def test_compute_portfolio_entry_pressure_summary_ignores_exits_and_rejections():
    records = [
        _entry("S1", "2026-05-01 09:00:00", status="SOLD"),
        {"status": "REJECTED", "side": "REJECTED", "strategy": "S2", "signal_time": "2026-05-01"},
    ]

    summary = compute_portfolio_entry_pressure_summary(records)

    assert summary["total_entry_count"] == 0
    assert summary["daily_entries"] == {}
    assert summary["intraday_windows"]["opening"]["max_entry_count"] == 0
    assert summary["warnings"] == []
