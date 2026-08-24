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


def test_records_without_a_usable_date_are_ignored():
    records = [
        _entry("S1", ""),
        {"status": "FILLED", "side": "BUY", "strategy": "S1", "code": "005930"},
    ]

    summary = compute_portfolio_entry_pressure_summary(records)

    assert summary["daily_entries"] == {}
    assert summary["total_entry_count"] == 0


def test_non_iso_date_falls_back_to_the_raw_prefix():
    from services.portfolio_entry_pressure_service import _record_date

    assert _record_date({"signal_time": "2026-05-01 09:30:00"}) == "2026-05-01"
    assert _record_date({"date": "20260501"}) == "2026-05-01"
    assert _record_date({"date": "2026/05/01"}) == "2026-05-01"
    assert _record_date({"signal_time": "봄철"}) == "봄철"
    assert _record_date({}) == ""


def test_intraday_window_is_none_outside_opening_and_closing():
    from services.portfolio_entry_pressure_service import _record_intraday_window

    assert _record_intraday_window({"signal_time": "2026-05-01 09:30:00"}) == "opening"
    assert _record_intraday_window({"signal_time": "2026-05-01 14:45:00"}) == "closing"
    assert _record_intraday_window({"signal_time": "2026-05-01 11:00:00"}) is None
    assert _record_intraday_window({"signal_time": "2026-05-01"}) is None


def test_record_time_accepts_iso_t_separator_and_pads_components():
    from services.portfolio_entry_pressure_service import _record_time

    assert _record_time({"signal_time": "2026-05-01T9:5"}) == "09:05:00"
    assert _record_time({"signal_time": "2026-05-01 09:30:15.123"}) == "09:30:15"
    assert _record_time({"signal_time": "2026-05-01"}) == ""
    assert _record_time({"signal_time": "2026-05-01 09"}) == ""
    assert _record_time({}) == ""
