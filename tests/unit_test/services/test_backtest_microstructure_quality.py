import pytest

from services.backtest_microstructure_quality import (
    MicrostructureQualityThresholds,
    format_optional_pct,
    summarize_capture_quality,
)


def test_summarize_capture_quality_uses_metadata_codes_and_program_db_fallbacks():
    payload = {
        "metadata": {
            "trade_date": "20260702",
            "codes": ["005930", "000660", "035420", "068270"],
            "program_source": "program_db",
            "program_fallback_codes": ["035420", "999999"],
            "quality": {
                "empty_minute_codes": ["068270"],
                "stale_minute_rows_dropped": {"005930": "2", "000660": None},
            },
        },
        "intraday_minutes": {
            "005930": [{}],
            "000660": [{}],
            "035420": [],
            "068270": [{}],
        },
        "execution_strength": {
            "005930": 120.0,
            "000660": 130.0,
            "035420": None,
            "068270": 110.0,
        },
        "program_trades": {
            "005930": {"program_net_buy_qty": 1},
            "000660": {"program_net_buy_qty": -1},
            "035420": {"program_net_buy_qty": 0},
            "068270": None,
        },
    }

    summary = summarize_capture_quality(
        payload,
        thresholds=MicrostructureQualityThresholds(max_stale_rows=0),
    )

    assert summary["trade_date"] == "20260702"
    assert summary["codes"] == 4
    assert summary["intraday_coverage_pct"] == pytest.approx(75.0)
    assert summary["execution_strength_coverage_pct"] == pytest.approx(75.0)
    assert summary["program_overlay_coverage_pct"] == pytest.approx(75.0)
    assert summary["program_db_available"] == 2
    assert summary["program_db_coverage_pct"] == pytest.approx(50.0)
    assert summary["program_fallback_codes"] == ["035420"]
    assert summary["stale_minute_rows_dropped"] == 2
    assert summary["issues"] == [
        "intraday_coverage_below_threshold",
        "program_overlay_coverage_below_threshold",
        "stale_minute_rows_present",
    ]


def test_summarize_capture_quality_falls_back_to_resolved_codes_when_metadata_missing():
    summary = summarize_capture_quality(
        {
            "metadata": {"program_source": "daily_rest"},
            "intraday_minutes": {"005930": [{}]},
            "execution_strength": {"005930": 120.0},
            "program_trades": {"005930": {"program_net_buy_qty": 1}},
        },
        fallback_codes=["005930"],
    )

    assert summary["codes"] == 1
    assert summary["quality_gate_passed"] is True


def test_format_optional_pct_formats_numbers_and_missing_values():
    assert format_optional_pct(12.345) == "12.3%"
    assert format_optional_pct(None) == "-"
