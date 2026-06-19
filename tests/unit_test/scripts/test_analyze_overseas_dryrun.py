"""해외 VBO dry-run shadow 분석기 단위 테스트 (해외 Phase 5 선행).

`OverseasVBODryRunService` 가 `signal_source="overseas_dryrun"` 로 남긴 would-be
신호(entry/exit/realized_pct)를 집계해 canary go/no-go 판단용 성과 리포트를 만든다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.analyze_overseas_dryrun import (
    compute_dryrun_report,
    compute_multiday_report,
    format_markdown_report,
    load_dryrun_records,
    load_ohlcv_dir,
    main,
    reconstruct_multiday_exit,
)


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def _write_jsonl(shadow_dir: Path, yyyymmdd: str, lines: list[dict]) -> Path:
    shadow_dir.mkdir(parents=True, exist_ok=True)
    path = shadow_dir / f"{yyyymmdd}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    return path


def _rec(*, code: str, exchange: str, date: str, realized_pct: float,
         exit_reason: str, qty: int | None = None, notional_usd: float | None = None,
         signal_source: str = "overseas_dryrun") -> dict:
    signal = {
        "code": code, "action": "BUY", "date": date,
        "entry_price": 100.0, "target": 100.0, "stop_price": 97.0,
        "exit_price": 100.0 * (1 + realized_pct / 100.0),
        "exit_reason": exit_reason, "realized_pct": realized_pct,
        "reason": "vbo_daily_breakout",
    }
    if qty is not None:
        signal["qty"] = qty
    if notional_usd is not None:
        signal["notional_usd"] = notional_usd
    return {
        "recorded_at": 1_700_000_000.0,
        "strategy": "LarryWilliamsVBO_overseas",
        "code": code,
        "signal_source": signal_source,
        "signal": signal,
        "snapshot": {"exchange": exchange, "avg_trading_value": 10_000_000.0},
    }


# ── load ─────────────────────────────────────────────────────────────────

def test_load_filters_signal_source_and_date_range(tmp_path):
    shadow = tmp_path / "event_shadow"
    _write_jsonl(shadow, "20260601", [
        _rec(code="AAA", exchange="NASD", date="20260601", realized_pct=5.0, exit_reason="eod"),
        # 다른 signal_source(국내 event shadow)는 제외
        _rec(code="BBB", exchange="NASD", date="20260601", realized_pct=1.0,
             exit_reason="eod", signal_source="event_shadow"),
    ])
    _write_jsonl(shadow, "20260605", [
        _rec(code="CCC", exchange="NYSE", date="20260605", realized_pct=-3.0, exit_reason="stop"),
    ])
    # 범위 밖 파일은 제외
    _write_jsonl(shadow, "20260610", [
        _rec(code="DDD", exchange="NASD", date="20260610", realized_pct=2.0, exit_reason="eod"),
    ])

    records = load_dryrun_records(shadow, "20260601", "20260605")

    codes = sorted(r["code"] for r in records)
    assert codes == ["AAA", "CCC"]
    aaa = next(r for r in records if r["code"] == "AAA")
    assert aaa["exchange"] == "NASD"
    assert aaa["trade_date"] == "20260601"
    assert aaa["realized_pct"] == 5.0
    assert aaa["exit_reason"] == "eod"


def test_load_returns_empty_when_dir_missing(tmp_path):
    assert load_dryrun_records(tmp_path / "nope", "20260601", "20260605") == []


# ── compute ──────────────────────────────────────────────────────────────

def test_compute_totals_and_win_rate():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 5.0, "exit_reason": "eod", "qty": 9, "notional_usd": 945.0},
        {"code": "BBB", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": -3.0, "exit_reason": "stop", "qty": 5, "notional_usd": 500.0},
        {"code": "CCC", "exchange": "NYSE", "trade_date": "20260602",
         "realized_pct": 1.0, "exit_reason": "eod", "qty": None, "notional_usd": None},
    ]

    report = compute_dryrun_report(records)

    t = report["totals"]
    assert t["signals"] == 3
    assert t["wins"] == 2
    assert t["losses"] == 1
    assert t["win_rate"] == pytest.approx(2 / 3)
    assert t["avg_realized_pct"] == pytest.approx((5.0 - 3.0 + 1.0) / 3)
    assert t["sum_realized_pct"] == pytest.approx(3.0)


def test_compute_by_exit_reason_and_exchange():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 5.0, "exit_reason": "eod", "qty": None, "notional_usd": None},
        {"code": "BBB", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": -3.0, "exit_reason": "stop", "qty": None, "notional_usd": None},
        {"code": "CCC", "exchange": "NYSE", "trade_date": "20260602",
         "realized_pct": 1.0, "exit_reason": "eod", "qty": None, "notional_usd": None},
    ]

    report = compute_dryrun_report(records)

    assert report["by_exit_reason"]["eod"] == 2
    assert report["by_exit_reason"]["stop"] == 1
    assert report["by_exchange"]["NASD"]["signals"] == 2
    assert report["by_exchange"]["NYSE"]["signals"] == 1
    assert report["by_exchange"]["NASD"]["sum_realized_pct"] == pytest.approx(2.0)


def test_compute_sizing_aggregates_only_when_present():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 5.0, "exit_reason": "eod", "qty": 9, "notional_usd": 945.0},
        {"code": "CCC", "exchange": "NYSE", "trade_date": "20260602",
         "realized_pct": 1.0, "exit_reason": "eod", "qty": None, "notional_usd": None},
    ]

    report = compute_dryrun_report(records)

    sz = report["sizing"]
    assert sz["sized_count"] == 1
    assert sz["total_notional_usd"] == pytest.approx(945.0)


def test_compute_krw_exposure_aggregates_only_when_present():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 5.0, "exit_reason": "eod", "qty": 9, "notional_usd": 945.0,
         "krw_exposure": 1_275_750.0},
        {"code": "BBB", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 2.0, "exit_reason": "eod", "qty": 4, "notional_usd": 400.0,
         "krw_exposure": 540_000.0},
        {"code": "CCC", "exchange": "NYSE", "trade_date": "20260602",
         "realized_pct": 1.0, "exit_reason": "eod", "qty": None, "notional_usd": None,
         "krw_exposure": None},
    ]

    report = compute_dryrun_report(records)

    sz = report["sizing"]
    assert sz["fx_sized_count"] == 2
    assert sz["total_krw_exposure"] == pytest.approx(1_815_750.0)
    assert sz["avg_krw_exposure"] == pytest.approx(907_875.0)


def test_compute_empty_records():
    report = compute_dryrun_report([])
    assert report["totals"]["signals"] == 0
    assert report["totals"]["win_rate"] is None


# ── markdown / main ────────────────────────────────────────────────────────

def test_format_markdown_smoke():
    report = compute_dryrun_report([
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "realized_pct": 5.0, "exit_reason": "eod", "qty": 9, "notional_usd": 945.0},
    ])
    md = format_markdown_report(report)
    assert "Overseas VBO Dry-run" in md
    assert "win_rate" in md


# ── multiday 회고 재구성 ─────────────────────────────────────────────────────

def test_reconstruct_stop_on_later_day():
    # entry 100, stop 97. day0 미터치, day1 저가 96 <= 97 → stop 청산(시가 미갭 → stop가)
    bars = [_bar("20260601", 100, 102, 98, 101), _bar("20260602", 100, 101, 96, 99)]
    r = reconstruct_multiday_exit(100.0, 97.0, bars, trailing_stop_pct=None, time_stop_days=None)
    assert r["exit_reason"] == "stop"
    assert r["exit_price"] == pytest.approx(97.0)
    assert r["holding_days"] == 1
    assert r["gross_return_pct"] == pytest.approx(-3.0)


def test_reconstruct_gap_through_stop_at_open():
    # day1 시가 95 < stop 97 로 갭다운 → 시가 체결(보수적)
    bars = [_bar("20260601", 100, 102, 98, 101), _bar("20260602", 95, 96, 94, 95)]
    r = reconstruct_multiday_exit(100.0, 97.0, bars, trailing_stop_pct=None, time_stop_days=None)
    assert r["exit_reason"] == "stop"
    assert r["exit_price"] == pytest.approx(95.0)
    assert r["gross_return_pct"] == pytest.approx(-5.0)


def test_reconstruct_trailing_exit():
    # entry 100, stop 95, trailing 10%. day0 고가 120 → peak 120, trail 108.
    # day1 저가 107 <= 108, 시가 119 > trail → trail 체결 108
    bars = [_bar("20260601", 100, 120, 118, 119), _bar("20260602", 119, 119, 107, 108)]
    r = reconstruct_multiday_exit(100.0, 95.0, bars, trailing_stop_pct=10.0, time_stop_days=None)
    assert r["exit_reason"] == "trailing"
    assert r["exit_price"] == pytest.approx(108.0)
    assert r["holding_days"] == 1
    assert r["gross_return_pct"] == pytest.approx(8.0)


def test_reconstruct_time_stop():
    # time_stop_days=2 → j>=2 인 day2 종가 청산(stop/trailing 미터치)
    bars = [_bar("20260601", 100, 101, 99, 100), _bar("20260602", 100, 102, 99, 101),
            _bar("20260603", 101, 103, 100, 102)]
    r = reconstruct_multiday_exit(100.0, 95.0, bars, trailing_stop_pct=None, time_stop_days=2)
    assert r["exit_reason"] == "time"
    assert r["exit_price"] == pytest.approx(102.0)
    assert r["holding_days"] == 2


def test_reconstruct_terminal_when_no_trigger():
    # 어떤 청산도 트리거되지 않으면 마지막 봉 종가 강제청산(terminal)
    bars = [_bar("20260601", 100, 101, 99, 100), _bar("20260602", 100, 103, 99, 102)]
    r = reconstruct_multiday_exit(100.0, 95.0, bars, trailing_stop_pct=None, time_stop_days=None)
    assert r["exit_reason"] == "terminal"
    assert r["exit_price"] == pytest.approx(102.0)
    assert r["holding_days"] == 1


def test_reconstruct_applies_round_trip_cost():
    bars = [_bar("20260601", 100, 103, 99, 102)]
    r = reconstruct_multiday_exit(100.0, 95.0, bars, trailing_stop_pct=None,
                                  time_stop_days=None, round_trip_cost_pct=0.2)
    assert r["gross_return_pct"] == pytest.approx(2.0)
    assert r["net_return_pct"] == pytest.approx(1.8)


def test_reconstruct_returns_none_on_no_bars_or_bad_entry():
    assert reconstruct_multiday_exit(100.0, 95.0, [], trailing_stop_pct=None, time_stop_days=None) is None
    assert reconstruct_multiday_exit(0.0, 0.0, [_bar("d", 1, 1, 1, 1)],
                                     trailing_stop_pct=None, time_stop_days=None) is None


def test_load_ohlcv_dir_reads_per_code_jsonl(tmp_path):
    d = tmp_path / "ohlcv"
    d.mkdir()
    with (d / "AAA.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps(_bar("20260602", 1, 2, 1, 2)) + "\n")
        f.write(json.dumps(_bar("20260601", 1, 2, 1, 2)) + "\n")
    by_code = load_ohlcv_dir(d)
    assert "AAA" in by_code
    # 날짜 오름차순 정렬
    assert [b["date"] for b in by_code["AAA"]] == ["20260601", "20260602"]


def test_load_ohlcv_dir_missing_returns_empty(tmp_path):
    assert load_ohlcv_dir(tmp_path / "nope") == {}


def test_compute_multiday_report_aggregates_and_gap():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "entry_price": 100.0, "stop_price": 95.0, "realized_pct": 2.0, "exit_reason": "eod"},
    ]
    # day0(진입일) 종가 102 → same-day +2. 이후 day1 고가 110 보유 시 종가 108 → +8
    ohlcv = {"AAA": [_bar("20260601", 100, 103, 99, 102), _bar("20260602", 102, 110, 101, 108)]}
    report = compute_multiday_report(
        records, ohlcv, trailing_stop_pct=None, time_stop_days=None, cost_pct=0.0,
    )
    assert report["reconstructed_count"] == 1
    assert report["unmatched_count"] == 0
    # terminal 보유 → +8
    assert report["gross_return_pct"]["avg"] == pytest.approx(8.0)
    gap = report["same_day_vs_multiday"]
    assert gap["same_day_avg_realized_pct"] == pytest.approx(2.0)
    assert gap["multiday_avg_gross_pct"] == pytest.approx(8.0)
    assert gap["gap_pct"] == pytest.approx(6.0)


def test_compute_multiday_report_counts_unmatched():
    records = [
        {"code": "AAA", "exchange": "NASD", "trade_date": "20260601",
         "entry_price": 100.0, "stop_price": 95.0, "realized_pct": 2.0, "exit_reason": "eod"},
        {"code": "ZZZ", "exchange": "NASD", "trade_date": "20260601",
         "entry_price": 100.0, "stop_price": 95.0, "realized_pct": 1.0, "exit_reason": "eod"},
    ]
    ohlcv = {"AAA": [_bar("20260601", 100, 103, 99, 102)]}  # ZZZ 없음
    report = compute_multiday_report(
        records, ohlcv, trailing_stop_pct=None, time_stop_days=None, cost_pct=0.0,
    )
    assert report["reconstructed_count"] == 1
    assert report["unmatched_count"] == 1


def test_main_writes_multiday_section_with_ohlcv_dir(tmp_path):
    shadow = tmp_path / "event_shadow"
    _write_jsonl(shadow, "20260601", [
        _rec(code="AAA", exchange="NASD", date="20260601", realized_pct=2.0, exit_reason="eod"),
    ])
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    with (ohlcv / "AAA.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps(_bar("20260601", 100, 103, 99, 102)) + "\n")
        f.write(json.dumps(_bar("20260602", 102, 110, 101, 108)) + "\n")
    out_json = tmp_path / "reports" / "overseas.json"

    rc = main([
        "--shadow-dir", str(shadow),
        "--date-from", "20260601", "--date-to", "20260601",
        "--ohlcv-dir", str(ohlcv),
        "--output-json", str(out_json),
    ])

    assert rc == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert "multiday" in report
    assert report["multiday"]["reconstructed_count"] == 1


def test_main_writes_json(tmp_path):
    shadow = tmp_path / "event_shadow"
    _write_jsonl(shadow, "20260601", [
        _rec(code="AAA", exchange="NASD", date="20260601", realized_pct=5.0, exit_reason="eod"),
        _rec(code="BBB", exchange="NYSE", date="20260601", realized_pct=-3.0, exit_reason="stop"),
    ])
    out_json = tmp_path / "reports" / "overseas.json"

    rc = main([
        "--shadow-dir", str(shadow),
        "--date-from", "20260601", "--date-to", "20260601",
        "--output-json", str(out_json),
    ])

    assert rc == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["totals"]["signals"] == 2
    assert report["totals"]["wins"] == 1
