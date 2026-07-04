import json

import pytest

from scripts.analyze_backtest_microstructure_quality import (
    compute_quality_report,
    format_markdown_report,
    load_capture_payloads,
    main,
)


def _payload(
    *,
    date: str,
    codes: list[str],
    intraday_minutes: dict,
    execution_strength: dict,
    program_trades: dict,
    program_source: str = "program_db",
    fallback_codes: list[str] | None = None,
    empty_codes: list[str] | None = None,
    stale_dropped: dict | None = None,
):
    return {
        "metadata": {
            "trade_date": date,
            "codes": codes,
            "program_source": program_source,
            "program_fallback_codes": fallback_codes or [],
            "quality": {
                "empty_minute_codes": empty_codes or [],
                "stale_minute_rows_dropped": stale_dropped or {},
            },
        },
        "intraday_minutes": intraday_minutes,
        "execution_strength": execution_strength,
        "program_trades": program_trades,
    }


def _write_capture(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_capture_payloads_filters_replay_files_and_date_range(tmp_path):
    _write_capture(
        tmp_path / "replay_microstructure_20260701.json",
        _payload(
            date="20260701",
            codes=["000001"],
            intraday_minutes={"000001": [{}]},
            execution_strength={"000001": 120.0},
            program_trades={"000001": {"program_net_buy_qty": 1}},
        ),
    )
    _write_capture(
        tmp_path / "replay_microstructure_20260702.json",
        _payload(
            date="20260702",
            codes=["000002"],
            intraday_minutes={"000002": [{}]},
            execution_strength={"000002": 130.0},
            program_trades={"000002": {"program_net_buy_qty": 1}},
        ),
    )
    _write_capture(tmp_path / "replay_program_trades_20260702.json", {"000002": 1})

    payloads = load_capture_payloads(tmp_path, date_from="20260702", date_to="20260702")

    assert [p["metadata"]["trade_date"] for p in payloads] == ["20260702"]


def test_compute_quality_report_flags_stale_rows_and_low_coverage():
    payloads = [
        _payload(
            date="20260701",
            codes=["000001", "000002"],
            intraday_minutes={"000001": [{}], "000002": [{}]},
            execution_strength={"000001": 120.0, "000002": 130.0},
            program_trades={
                "000001": {"program_net_buy_qty": 100},
                "000002": {"program_net_buy_qty": -50},
            },
        ),
        _payload(
            date="20260702",
            codes=["000001", "000002", "000003", "000004"],
            intraday_minutes={"000001": [{}], "000002": [], "000003": [], "000004": [{}]},
            execution_strength={"000001": 120.0, "000002": None, "000003": None, "000004": 99.0},
            program_trades={
                "000001": {"program_net_buy_qty": 100},
                "000002": None,
                "000003": {"program_net_buy_qty": -20},
                "000004": None,
            },
            fallback_codes=["000003"],
            empty_codes=["000002", "000003"],
            stale_dropped={"000004": 3},
        ),
    ]

    report = compute_quality_report(
        payloads,
        min_intraday_coverage_pct=80.0,
        min_program_overlay_coverage_pct=80.0,
        min_program_db_coverage_pct=50.0,
        max_stale_rows=0,
    )

    assert report["totals"]["capture_files"] == 2
    assert report["totals"]["total_codes"] == 6
    assert report["totals"]["intraday_coverage_pct"] == pytest.approx(4 / 6 * 100)
    assert report["totals"]["program_overlay_coverage_pct"] == pytest.approx(4 / 6 * 100)
    assert report["totals"]["program_db_coverage_pct"] == pytest.approx(3 / 6 * 100)
    assert report["totals"]["stale_minute_rows_dropped"] == 3
    assert report["totals"]["quality_gate_passed"] is False

    bad_day = report["by_date"]["20260702"]
    assert bad_day["intraday_coverage_pct"] == pytest.approx(50.0)
    assert bad_day["program_overlay_coverage_pct"] == pytest.approx(50.0)
    assert bad_day["program_db_coverage_pct"] == pytest.approx(25.0)
    assert bad_day["stale_minute_rows_dropped"] == 3
    assert "intraday_coverage_below_threshold" in bad_day["issues"]
    assert "program_overlay_coverage_below_threshold" in bad_day["issues"]
    assert "program_db_coverage_below_threshold" in bad_day["issues"]
    assert "stale_minute_rows_present" in bad_day["issues"]


def test_compute_quality_report_passes_when_payloads_meet_thresholds():
    report = compute_quality_report([
        _payload(
            date="20260701",
            codes=["000001", "000002"],
            intraday_minutes={"000001": [{}], "000002": [{}]},
            execution_strength={"000001": 120.0, "000002": 130.0},
            program_trades={
                "000001": {"program_net_buy_qty": 100},
                "000002": {"program_net_buy_qty": -50},
            },
        ),
    ])

    assert report["totals"]["quality_gate_passed"] is True
    assert report["by_date"]["20260701"]["issues"] == []


def test_format_markdown_report_contains_gate_and_date_rows():
    report = compute_quality_report([
        _payload(
            date="20260701",
            codes=["000001"],
            intraday_minutes={"000001": [{}]},
            execution_strength={"000001": 120.0},
            program_trades={"000001": {"program_net_buy_qty": 100}},
        ),
    ])

    md = format_markdown_report(report)

    assert "Microstructure Capture Quality" in md
    assert "quality_gate_passed" in md
    assert "20260701" in md


def test_main_writes_json_and_markdown(tmp_path):
    _write_capture(
        tmp_path / "replay_microstructure_20260701.json",
        _payload(
            date="20260701",
            codes=["000001"],
            intraday_minutes={"000001": [{}]},
            execution_strength={"000001": 120.0},
            program_trades={"000001": {"program_net_buy_qty": 100}},
        ),
    )
    out_json = tmp_path / "reports" / "quality.json"
    out_md = tmp_path / "reports" / "quality.md"

    rc = main([
        "--input-dir", str(tmp_path),
        "--date-from", "20260701",
        "--date-to", "20260701",
        "--output-json", str(out_json),
        "--output-markdown", str(out_md),
    ])

    assert rc == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["totals"]["capture_files"] == 1
    assert "20260701" in out_md.read_text(encoding="utf-8")


def test_main_fail_on_gate_returns_nonzero_for_bad_quality(tmp_path):
    _write_capture(
        tmp_path / "replay_microstructure_20260701.json",
        _payload(
            date="20260701",
            codes=["000001"],
            intraday_minutes={"000001": []},
            execution_strength={"000001": None},
            program_trades={"000001": None},
            empty_codes=["000001"],
        ),
    )

    rc = main([
        "--input-dir", str(tmp_path),
        "--date-from", "20260701",
        "--date-to", "20260701",
        "--fail-on-gate",
    ])

    assert rc == 1


def test_compute_quality_report_aggregates_execution_strength_db_coverage():
    report = compute_quality_report([
        {
            "metadata": {
                "trade_date": "20260703",
                "codes": ["A", "B"],
                "execution_strength_source": "es_db",
            },
            "intraday_minutes": {"A": [{"r": 1}], "B": [{"r": 1}]},
            "execution_strength": {"A": 1.0, "B": 1.0},
            "execution_strength_intraday": {
                "A": [{"time": "090001", "strength": 1.0}],
                "B": [],
            },
            "program_trades": {"A": {"q": 1}, "B": {"q": 1}},
        },
    ])

    assert report["totals"]["execution_strength_db_coverage_pct"] == pytest.approx(50.0)
    assert report["by_date"]["20260703"][
        "execution_strength_db_coverage_pct"
    ] == pytest.approx(50.0)

    md = format_markdown_report(report)
    assert "execution_strength_db_coverage" in md


def test_execution_strength_db_coverage_absent_for_rest_scalar_payloads():
    report = compute_quality_report([
        _payload(
            date="20260701",
            codes=["A"],
            intraday_minutes={"A": [{"r": 1}]},
            execution_strength={"A": 1.0},
            program_trades={"A": {"q": 1}},
        ),
    ])

    assert report["totals"]["execution_strength_db_coverage_pct"] is None
