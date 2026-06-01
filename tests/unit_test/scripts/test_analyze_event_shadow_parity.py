"""P2 2-4 PR-2.5 선행: shadow ↔ polling parity 분석기 단위 테스트."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

from scripts.analyze_event_shadow_parity import (
    compute_parity_report,
    format_markdown_report,
    load_polling_records,
    load_polling_sells,
    load_shadow_records,
    main,
)


_KST = ZoneInfo("Asia/Seoul")


def _kst_epoch(yyyymmdd: str, hh: int, mm: int, ss: int = 0) -> float:
    dt = datetime(
        int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]),
        hh, mm, ss, tzinfo=_KST,
    )
    return dt.timestamp()


def _kst_ts_str(yyyymmdd: str, hh: int, mm: int, ss: int = 0) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]} {hh:02d}:{mm:02d}:{ss:02d}"


def _write_shadow_jsonl(shadow_dir: Path, yyyymmdd: str, lines: list[dict]) -> Path:
    shadow_dir.mkdir(parents=True, exist_ok=True)
    path = shadow_dir / f"{yyyymmdd}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    return path


def _shadow_record(*, strategy: str, code: str, recorded_at: float,
                   price: int = 10000, action: str = "BUY",
                   signal_source: str = "event_shadow",
                   include_signal_source: bool = True) -> dict:
    rec = {
        "recorded_at": recorded_at,
        "strategy": strategy,
        "code": code,
        "signal": {"code": code, "name": code, "action": action, "price": price,
                   "strategy_name": strategy, "reason": "test"},
        "snapshot": {"price": price, "open": price - 100},
    }
    if include_signal_source:
        rec["signal_source"] = signal_source
    return rec


def _create_scheduler_db(db_path: Path, rows: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS signal_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT    NOT NULL,
                code          TEXT    NOT NULL,
                name          TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                price         INTEGER NOT NULL,
                qty           INTEGER NOT NULL DEFAULT 1,
                return_rate   REAL,
                reason        TEXT,
                timestamp     TEXT    NOT NULL,
                api_success   INTEGER NOT NULL DEFAULT 1
            )"""
        )
        for r in rows:
            conn.execute(
                """INSERT INTO signal_history
                   (strategy_name, code, name, action, price, qty,
                    return_rate, reason, timestamp, api_success)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r.get("strategy_name", "래리윌리엄스VBO"),
                    r["code"],
                    r.get("name", r["code"]),
                    r.get("action", "BUY"),
                    r.get("price", 10000),
                    r.get("qty", 1),
                    r.get("return_rate"),
                    r.get("reason", "test"),
                    r["timestamp"],
                    1 if r.get("api_success", True) else 0,
                ),
            )
        conn.commit()


# ── load_shadow_records ────────────────────────────────────────────────────

def test_load_shadow_records_filters_by_date_range_and_strategy(tmp_path):
    shadow_dir = tmp_path / "shadow"
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 9, 15)),
        _shadow_record(strategy="OtherStrategy", code="000002",
                       recorded_at=_kst_epoch("20260520", 9, 16)),
    ])
    _write_shadow_jsonl(shadow_dir, "20260521", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000003",
                       recorded_at=_kst_epoch("20260521", 9, 20)),
    ])
    # out of range
    _write_shadow_jsonl(shadow_dir, "20260522", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000004",
                       recorded_at=_kst_epoch("20260522", 9, 25)),
    ])

    recs = load_shadow_records(
        shadow_dir=shadow_dir,
        date_from="20260520",
        date_to="20260521",
        strategy_filter="래리윌리엄스VBO",
    )

    codes = sorted(r.code for r in recs)
    assert codes == ["000001", "000003"]
    assert all(r.source == "event_shadow" for r in recs)
    assert all(r.action == "BUY" for r in recs)
    assert all(r.strategy == "래리윌리엄스VBO" for r in recs)


def test_load_shadow_records_filters_by_signal_source(tmp_path):
    """P2 2-4 exit: signal_source 로 entry/exit shadow 레코드를 분리한다."""
    shadow_dir = tmp_path / "shadow"
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 9, 15),
                       action="BUY", signal_source="event_shadow"),
        _shadow_record(strategy="VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 13, 0),
                       action="SELL", signal_source="event_shadow_exit"),
    ])
    common = dict(shadow_dir=shadow_dir, date_from="20260520", date_to="20260520",
                  strategy_filter="VBO")

    entry = load_shadow_records(**common, signal_source="event_shadow")
    assert [r.action for r in entry] == ["BUY"]

    exit_recs = load_shadow_records(**common, signal_source="event_shadow_exit")
    assert [r.action for r in exit_recs] == ["SELL"]

    both = load_shadow_records(**common)  # 필터 없음 → 둘 다
    assert len(both) == 2


def test_load_shadow_records_treats_missing_signal_source_as_entry(tmp_path):
    """signal_source 필드가 없는 레거시 레코드는 entry(event_shadow)로 간주한다."""
    shadow_dir = tmp_path / "shadow"
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 9, 15),
                       include_signal_source=False),
    ])
    recs = load_shadow_records(shadow_dir=shadow_dir, date_from="20260520",
                               date_to="20260520", strategy_filter="VBO",
                               signal_source="event_shadow")
    assert [r.code for r in recs] == ["000001"]


# ── load_polling_records ───────────────────────────────────────────────────

def test_load_polling_records_filters_by_strategy_action_success(tmp_path):
    db_path = tmp_path / "scheduler.db"
    _create_scheduler_db(db_path, [
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 9, 15),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": True},
        # 다른 전략
        {"code": "000002", "timestamp": _kst_ts_str("20260520", 9, 16),
         "strategy_name": "OtherStrategy", "action": "BUY", "api_success": True},
        # SELL — 매칭 대상 아님
        {"code": "000003", "timestamp": _kst_ts_str("20260520", 14, 30),
         "strategy_name": "래리윌리엄스VBO", "action": "SELL", "api_success": True},
        # api_success=False — 매칭 대상 아님
        {"code": "000004", "timestamp": _kst_ts_str("20260520", 9, 20),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": False},
        # 날짜 범위 밖
        {"code": "000005", "timestamp": _kst_ts_str("20260522", 9, 25),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": True},
    ])

    recs = load_polling_records(
        db_path=db_path,
        date_from="20260520",
        date_to="20260521",
        strategy_filter="래리윌리엄스VBO",
        action="BUY",
        require_api_success=True,
    )

    codes = sorted(r.code for r in recs)
    assert codes == ["000001"]
    assert recs[0].source == "polling"


# ── compute_parity_report ──────────────────────────────────────────────────

def _polling_rec(code: str, ts_epoch: float, strategy: str = "래리윌리엄스VBO",
                 price: Optional[float] = None):
    from scripts.analyze_event_shadow_parity import ParityRecord
    kst_dt = datetime.fromtimestamp(ts_epoch, tz=_KST)
    raw: dict = {}
    if price is not None:
        raw["price"] = price
    return ParityRecord(
        source="polling", strategy=strategy, code=code, action="BUY",
        ts_epoch=ts_epoch, ts_iso=kst_dt.isoformat(),
        trade_date=kst_dt.strftime("%Y%m%d"),
        raw=raw,
    )


def _shadow_rec(code: str, ts_epoch: float, strategy: str = "래리윌리엄스VBO",
                price: Optional[float] = None):
    from scripts.analyze_event_shadow_parity import ParityRecord
    kst_dt = datetime.fromtimestamp(ts_epoch, tz=_KST)
    raw: dict = {}
    if price is not None:
        raw["signal"] = {"price": price}
    return ParityRecord(
        source="event_shadow", strategy=strategy, code=code, action="BUY",
        ts_epoch=ts_epoch, ts_iso=kst_dt.isoformat(),
        trade_date=kst_dt.strftime("%Y%m%d"),
        raw=raw,
    )


def test_compute_parity_report_matched_within_window():
    p_ts = _kst_epoch("20260520", 9, 15, 30)
    s_ts = _kst_epoch("20260520", 9, 15, 35)  # 5초 차이
    polling = [_polling_rec("000001", p_ts)]
    shadow = [_shadow_rec("000001", s_ts)]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    assert report["totals"]["matched"] == 1
    assert report["totals"]["shadow_only"] == 0
    assert report["totals"]["polling_only"] == 0
    assert report["totals"]["match_rate"] == pytest.approx(1.0)


def test_compute_parity_report_shadow_only_classified_as_over_fire():
    s_ts = _kst_epoch("20260520", 9, 15, 35)
    shadow = [_shadow_rec("000001", s_ts)]
    polling: list = []

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    assert report["totals"]["shadow_only"] == 1
    assert report["totals"]["matched"] == 0
    assert report["totals"]["polling_only"] == 0
    # shadow-only 는 fast path over-fire 라벨링
    assert report["details"]["shadow_only"][0]["code"] == "000001"


def test_compute_parity_report_polling_only_classified_as_miss():
    p_ts = _kst_epoch("20260520", 9, 15, 30)
    polling = [_polling_rec("000001", p_ts)]
    shadow: list = []

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    assert report["totals"]["polling_only"] == 1
    assert report["totals"]["matched"] == 0
    assert report["totals"]["shadow_only"] == 0
    assert report["details"]["polling_only"][0]["code"] == "000001"


def test_compute_parity_report_lead_time_negative_when_shadow_earlier():
    # shadow 가 polling 보다 3초 빨라야 함 → lead_time = shadow - polling = -3
    s_ts = _kst_epoch("20260520", 9, 15, 30)
    p_ts = _kst_epoch("20260520", 9, 15, 33)
    polling = [_polling_rec("000001", p_ts)]
    shadow = [_shadow_rec("000001", s_ts)]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    stats = report["lead_time_seconds"]
    assert stats["count"] == 1
    assert stats["avg"] == pytest.approx(-3.0)
    assert stats["min"] == pytest.approx(-3.0)
    assert stats["max"] == pytest.approx(-3.0)


def test_compute_parity_report_duplicate_signals_counted_per_source():
    base = _kst_epoch("20260520", 9, 15, 30)
    shadow = [
        _shadow_rec("000001", base),
        _shadow_rec("000001", base + 1.0),  # 같은 그룹키 → duplicate
    ]
    polling = [
        _polling_rec("000001", base + 2.0),
        _polling_rec("000001", base + 3.0),  # duplicate
        _polling_rec("000001", base + 4.0),  # duplicate
    ]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    assert report["totals"]["matched"] == 1
    assert report["totals"]["duplicates_shadow"] == 1
    assert report["totals"]["duplicates_polling"] == 2


def test_compute_parity_report_match_window_excludes_distant_signals():
    # 5분 윈도우인데 10분 차이 → 매칭 실패, 둘 다 _only 로 분류
    p_ts = _kst_epoch("20260520", 9, 15, 0)
    s_ts = _kst_epoch("20260520", 9, 25, 0)
    polling = [_polling_rec("000001", p_ts)]
    shadow = [_shadow_rec("000001", s_ts)]

    report = compute_parity_report(shadow, polling, match_window_sec=300.0)

    assert report["totals"]["matched"] == 0
    assert report["totals"]["shadow_only"] == 1
    assert report["totals"]["polling_only"] == 1


def test_compute_parity_report_per_date_breakdown():
    p1 = _kst_epoch("20260520", 9, 15)
    s1 = _kst_epoch("20260520", 9, 15)
    p2 = _kst_epoch("20260521", 10, 0)
    shadow = [_shadow_rec("000001", s1)]
    polling = [_polling_rec("000001", p1), _polling_rec("000002", p2)]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    by_date = report["per_date"]
    assert by_date["20260520"]["matched"] == 1
    assert by_date["20260520"]["polling_only"] == 0
    assert by_date["20260521"]["polling_only"] == 1


# ── price divergence ───────────────────────────────────────────────────────

def test_price_divergence_for_matched_pairs_uses_raw_prices():
    # shadow 10100 vs polling 10000 → diff +100, diff_pct +1.0
    base = _kst_epoch("20260520", 9, 15, 30)
    shadow = [_shadow_rec("000001", base, price=10100)]
    polling = [_polling_rec("000001", base + 1, price=10000)]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    div = report["price_divergence"]
    assert div["count"] == 1
    assert div["avg_diff"] == pytest.approx(100.0)
    assert div["avg_diff_pct"] == pytest.approx(1.0)
    assert div["min_diff_pct"] == pytest.approx(1.0)
    assert div["max_diff_pct"] == pytest.approx(1.0)


def test_price_divergence_empty_when_no_matched_pairs():
    base = _kst_epoch("20260520", 9, 15, 30)
    shadow = [_shadow_rec("000001", base, price=10100)]
    polling: list = []

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    div = report["price_divergence"]
    assert div["count"] == 0
    assert div["avg_diff"] is None
    assert div["avg_diff_pct"] is None


def test_price_divergence_skips_pairs_without_price():
    base = _kst_epoch("20260520", 9, 15, 30)
    # shadow has price, polling doesn't
    shadow = [_shadow_rec("000001", base, price=10100)]
    polling = [_polling_rec("000001", base + 1, price=None)]

    report = compute_parity_report(shadow, polling, match_window_sec=60.0)

    assert report["totals"]["matched"] == 1
    # 가격 누락 → 괴리 통계에서 제외
    assert report["price_divergence"]["count"] == 0


# ── missed trade PnL ───────────────────────────────────────────────────────

def test_load_polling_sells_returns_return_rates_grouped_by_key(tmp_path):
    db_path = tmp_path / "scheduler.db"
    _create_scheduler_db(db_path, [
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 9, 15),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": True},
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 14, 30),
         "strategy_name": "래리윌리엄스VBO", "action": "SELL", "api_success": True,
         "return_rate": 2.5},
        # 다른 날
        {"code": "000002", "timestamp": _kst_ts_str("20260521", 13, 0),
         "strategy_name": "래리윌리엄스VBO", "action": "SELL", "api_success": True,
         "return_rate": -1.2},
        # return_rate NULL → 제외
        {"code": "000003", "timestamp": _kst_ts_str("20260520", 14, 0),
         "strategy_name": "래리윌리엄스VBO", "action": "SELL", "api_success": True,
         "return_rate": None},
    ])

    sells = load_polling_sells(
        db_path=db_path,
        date_from="20260520",
        date_to="20260521",
        strategy_filter="래리윌리엄스VBO",
    )

    assert ("래리윌리엄스VBO", "000001", "20260520") in sells
    assert sells[("래리윌리엄스VBO", "000001", "20260520")] == [2.5]
    assert sells[("래리윌리엄스VBO", "000002", "20260521")] == [-1.2]
    assert ("래리윌리엄스VBO", "000003", "20260520") not in sells


def test_missed_pnl_uses_sell_return_rate_for_polling_only():
    p_ts = _kst_epoch("20260520", 9, 15, 30)
    polling = [_polling_rec("000001", p_ts)]
    shadow: list = []
    sells = {("래리윌리엄스VBO", "000001", "20260520"): [3.0]}

    report = compute_parity_report(
        shadow, polling, match_window_sec=60.0,
        polling_sells_by_key=sells,
    )

    mp = report["missed_pnl"]
    assert mp["known_count"] == 1
    assert mp["unknown_count"] == 0
    assert mp["avg_return_rate"] == pytest.approx(3.0)
    assert mp["sum_return_rate"] == pytest.approx(3.0)


def test_missed_pnl_marks_unknown_when_no_matching_sell():
    p_ts = _kst_epoch("20260520", 9, 15, 30)
    polling = [_polling_rec("000001", p_ts)]
    shadow: list = []

    report = compute_parity_report(
        shadow, polling, match_window_sec=60.0,
        polling_sells_by_key={},
    )

    mp = report["missed_pnl"]
    assert mp["known_count"] == 0
    assert mp["unknown_count"] == 1
    assert mp["avg_return_rate"] is None


def test_missed_pnl_only_counts_polling_only_not_matched():
    # matched 는 fast path 도 잡았으니 missed 아님
    p_ts = _kst_epoch("20260520", 9, 15, 30)
    s_ts = _kst_epoch("20260520", 9, 15, 31)
    polling = [_polling_rec("000001", p_ts)]
    shadow = [_shadow_rec("000001", s_ts)]
    sells = {("래리윌리엄스VBO", "000001", "20260520"): [5.0]}

    report = compute_parity_report(
        shadow, polling, match_window_sec=60.0,
        polling_sells_by_key=sells,
    )

    assert report["totals"]["matched"] == 1
    assert report["missed_pnl"]["known_count"] == 0
    assert report["missed_pnl"]["unknown_count"] == 0


# ── main / CLI ─────────────────────────────────────────────────────────────

def test_main_writes_json_and_markdown_outputs(tmp_path, capsys):
    shadow_dir = tmp_path / "shadow"
    db_path = tmp_path / "scheduler.db"

    s_ts = _kst_epoch("20260520", 9, 15, 30)
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000001", recorded_at=s_ts),
    ])
    _create_scheduler_db(db_path, [
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 9, 15, 30),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": True},
    ])

    json_out = tmp_path / "report.json"
    md_out = tmp_path / "report.md"
    exit_code = main([
        "--shadow-dir", str(shadow_dir),
        "--scheduler-db", str(db_path),
        "--date-from", "20260520",
        "--date-to", "20260520",
        "--strategy", "래리윌리엄스VBO",
        "--match-window-sec", "60",
        "--output-json", str(json_out),
        "--output-markdown", str(md_out),
    ])
    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["totals"]["matched"] == 1
    md_text = md_out.read_text(encoding="utf-8")
    assert "matched" in md_text.lower() or "전체" in md_text
    # stdout 에 output 경로가 적어도 노출되어야 함
    out = capsys.readouterr().out
    assert str(json_out) in out


def test_main_entry_default_excludes_exit_shadow_records(tmp_path):
    """기본(entry) 실행은 같은 jsonl 의 exit shadow(SELL) 레코드에 오염되지 않는다."""
    shadow_dir = tmp_path / "shadow"
    db_path = tmp_path / "scheduler.db"
    s_ts = _kst_epoch("20260520", 9, 15, 30)
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000001", recorded_at=s_ts),
        # 같은 종목/날짜의 exit shadow → entry 분석에서 제외되어야 함
        _shadow_record(strategy="래리윌리엄스VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 13, 0),
                       action="SELL", signal_source="event_shadow_exit"),
    ])
    _create_scheduler_db(db_path, [
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 9, 15, 30),
         "strategy_name": "래리윌리엄스VBO", "action": "BUY", "api_success": True},
    ])
    json_out = tmp_path / "report.json"
    exit_code = main([
        "--shadow-dir", str(shadow_dir), "--scheduler-db", str(db_path),
        "--date-from", "20260520", "--date-to", "20260520",
        "--strategy", "래리윌리엄스VBO", "--match-window-sec", "60",
        "--output-json", str(json_out),
    ])
    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["totals"]["matched"] == 1
    # exit SELL 레코드가 섞였다면 shadow_only=1 로 오염됐을 것
    assert payload["totals"]["shadow_only"] == 0
    assert payload["config"]["signal_source"] == "event_shadow"


def test_main_exit_mode_matches_sell_shadow_with_polling_sell(tmp_path):
    """exit 모드: exit shadow(SELL) ↔ polling SELL 매칭."""
    shadow_dir = tmp_path / "shadow"
    db_path = tmp_path / "scheduler.db"
    s_ts = _kst_epoch("20260520", 13, 0, 5)
    _write_shadow_jsonl(shadow_dir, "20260520", [
        _shadow_record(strategy="래리윌리엄스VBO", code="000001", recorded_at=s_ts,
                       action="SELL", signal_source="event_shadow_exit"),
        # entry 레코드는 exit 분석에서 제외
        _shadow_record(strategy="래리윌리엄스VBO", code="000001",
                       recorded_at=_kst_epoch("20260520", 9, 15)),
    ])
    _create_scheduler_db(db_path, [
        {"code": "000001", "timestamp": _kst_ts_str("20260520", 13, 0, 0),
         "strategy_name": "래리윌리엄스VBO", "action": "SELL", "api_success": True,
         "return_rate": -3.1},
    ])
    json_out = tmp_path / "report.json"
    exit_code = main([
        "--shadow-dir", str(shadow_dir), "--scheduler-db", str(db_path),
        "--date-from", "20260520", "--date-to", "20260520",
        "--strategy", "래리윌리엄스VBO", "--match-window-sec", "60",
        "--signal-source", "event_shadow_exit", "--polling-action", "SELL",
        "--output-json", str(json_out),
    ])
    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["totals"]["matched"] == 1
    assert payload["totals"]["shadow_only"] == 0
    assert payload["totals"]["polling_only"] == 0
    assert payload["config"]["signal_source"] == "event_shadow_exit"
    assert payload["config"]["polling_action"] == "SELL"


def test_format_markdown_report_basic_structure():
    fake_report = {
        "totals": {
            "matched": 3, "shadow_only": 1, "polling_only": 2,
            "duplicates_shadow": 0, "duplicates_polling": 0,
            "match_rate": 0.5,
        },
        "lead_time_seconds": {"count": 3, "avg": -2.0, "min": -5.0, "max": 1.0,
                              "median": -2.0},
        "per_date": {
            "20260520": {"matched": 3, "shadow_only": 1, "polling_only": 2,
                         "duplicates_shadow": 0, "duplicates_polling": 0},
        },
        "details": {"shadow_only": [], "polling_only": []},
        "config": {"strategy": "래리윌리엄스VBO", "match_window_sec": 60.0,
                   "date_from": "20260520", "date_to": "20260520"},
    }
    md = format_markdown_report(fake_report)
    assert "래리윌리엄스VBO" in md
    assert "matched" in md.lower() or "전체" in md
