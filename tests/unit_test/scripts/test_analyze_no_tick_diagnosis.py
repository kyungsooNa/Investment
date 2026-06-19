"""P2 2-4 무틱 a1/a2 판정기 단위 테스트.

합성 로그(streaming missing_reason + event_shadow tick_ingest 스냅샷)로
a1(KIS 미전송)·a2(quality 게이트 탈락)·a3(측정 갭)·정상·not_subscribed 케이스를 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.analyze_no_tick_diagnosis import (
    A1,
    A2,
    A3,
    RECEIVED_NOT_DISPATCHED,
    UNKNOWN_NO_SNAPSHOT,
    classify_code,
    compute_no_tick_report,
    format_markdown_report,
    load_missing_reasons,
    load_tick_ingest_snapshots,
    main,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")


def _streaming_line(action: str, code: str = "", reason: str = "") -> dict:
    """JsonFormatter on-disk 형태: data 필드에 dict msg."""
    data = {"action": action}
    if code:
        data["code"] = code
    if reason:
        data["reason"] = reason
    return {"timestamp": "2026-06-19 10:00:00,000", "level": "WARNING",
            "name": "streaming", "data": data}


def _shadow_status(recorded_at: float, tick_ingest: dict,
                   strategy: str = "래리윌리엄스VBO") -> dict:
    return {
        "recorded_at": recorded_at,
        "strategy": strategy,
        "signal_source": "event_shadow_status",
        "event": "subscriptions_refreshed",
        "details": {"candidate_count": len(tick_ingest), "tick_ingest": tick_ingest},
    }


# ── classify_code (단위) ──────────────────────────────────────────────────────

def test_classify_a1_received_zero():
    assert classify_code({"received": 0, "quality_reject": 0, "dispatched": 0}) == A1


def test_classify_a2_quality_reject_all():
    assert classify_code({"received": 50, "quality_reject": 50, "dispatched": 0}) == A2


def test_classify_a3_dispatched_present():
    # 디스패치됐는데 워치독이 no-tick 으로 표시 → 측정 갭
    assert classify_code({"received": 50, "quality_reject": 0, "dispatched": 50}) == A3


def test_classify_received_not_dispatched():
    assert classify_code({"received": 5, "quality_reject": 0, "dispatched": 0}) == RECEIVED_NOT_DISPATCHED


def test_classify_unknown_no_snapshot():
    assert classify_code(None) == UNKNOWN_NO_SNAPSHOT


# ── loaders ───────────────────────────────────────────────────────────────────

def test_load_missing_reasons_counts_per_code(tmp_path):
    log = tmp_path / "1_streaming.log.json"
    _write_jsonl(log, [
        _streaming_line("missing_reason", "A0001", "subscribed_no_tick"),
        _streaming_line("missing_reason", "A0001", "subscribed_no_tick"),
        _streaming_line("missing_reason", "B0002", "not_subscribed"),
        _streaming_line("connect"),  # 무관 액션 무시
        _streaming_line("missing_reason", "C0003", "subscribed_no_tick"),
    ])
    out = load_missing_reasons([log])
    assert out["A0001"]["subscribed_no_tick"] == 2
    assert out["B0002"]["not_subscribed"] == 1
    assert out["C0003"]["subscribed_no_tick"] == 1


def test_load_missing_reasons_skips_nonjson_and_missing_data(tmp_path):
    log = tmp_path / "1_streaming.log.json"
    log.write_text(
        "not-json-line\n"
        + json.dumps({"timestamp": "t", "level": "INFO", "message": "plain"}) + "\n"
        + json.dumps(_streaming_line("missing_reason", "A0001", "subscribed_no_tick")) + "\n",
        encoding="utf-8",
    )
    out = load_missing_reasons([log])
    assert out == {"A0001": {"subscribed_no_tick": 1}}


def test_load_tick_ingest_keeps_latest_cumulative_snapshot(tmp_path):
    shadow_dir = tmp_path / "event_shadow"
    _write_jsonl(shadow_dir / "20260619.jsonl", [
        _shadow_status(1000.0, {"A0001": {"received": 0, "quality_reject": 0, "dispatched": 0}}),
        # 더 늦은 스냅샷이 누적값 — 이쪽이 채택돼야 한다
        _shadow_status(2000.0, {"A0001": {"received": 0, "quality_reject": 0, "dispatched": 0},
                                "B0002": {"received": 100, "quality_reject": 100, "dispatched": 0}}),
    ])
    out = load_tick_ingest_snapshots(shadow_dir, "20260619", "20260619")
    assert out["A0001"]["recorded_at"] == 2000.0
    assert out["B0002"]["received"] == 100


def test_load_tick_ingest_respects_date_and_strategy_filter(tmp_path):
    shadow_dir = tmp_path / "event_shadow"
    _write_jsonl(shadow_dir / "20260618.jsonl", [
        _shadow_status(900.0, {"OLD01": {"received": 5, "quality_reject": 0, "dispatched": 5}}),
    ])
    _write_jsonl(shadow_dir / "20260619.jsonl", [
        _shadow_status(2000.0, {"A0001": {"received": 0, "quality_reject": 0, "dispatched": 0}},
                       strategy="래리윌리엄스VBO"),
        _shadow_status(2100.0, {"OTH01": {"received": 1, "quality_reject": 0, "dispatched": 1}},
                       strategy="다른전략"),
    ])
    out = load_tick_ingest_snapshots(shadow_dir, "20260619", "20260619",
                                     strategy_filter="래리윌리엄스VBO")
    assert "A0001" in out
    assert "OLD01" not in out  # 날짜 범위 밖
    assert "OTH01" not in out  # 전략 필터 밖


# ── compute_no_tick_report (통합) ─────────────────────────────────────────────

def test_compute_report_classifies_each_bucket():
    missing_reasons = {
        "A1CODE": {"subscribed_no_tick": 10},   # received==0 → a1
        "A2CODE": {"subscribed_no_tick": 8},     # quality reject → a2
        "A3CODE": {"subscribed_no_tick": 3},     # dispatched>0 → a3
        "NOSNAP": {"subscribed_no_tick": 1},     # 스냅샷 없음
        "NOTSUB": {"not_subscribed": 5},          # ack 미확정 → 별도 버킷
    }
    tick_snaps = {
        "A1CODE": {"received": 0, "quality_reject": 0, "dispatched": 0, "recorded_at": 1.0},
        "A2CODE": {"received": 40, "quality_reject": 40, "dispatched": 0, "recorded_at": 1.0},
        "A3CODE": {"received": 30, "quality_reject": 0, "dispatched": 30, "recorded_at": 1.0},
    }
    report = compute_no_tick_report(missing_reasons, tick_snaps)
    s = report["summary"]
    assert s["total_no_tick_codes"] == 4  # NOTSUB 제외
    assert s["not_subscribed_only_codes"] == 1
    assert s["classification_counts"][A1] == 1
    assert s["classification_counts"][A2] == 1
    assert s["classification_counts"][A3] == 1
    assert s["classification_counts"][UNKNOWN_NO_SNAPSHOT] == 1
    assert report["not_subscribed_only"] == ["NOTSUB"]


def test_compute_report_verdict_is_dominant_class():
    missing_reasons = {f"A{i}": {"subscribed_no_tick": 1} for i in range(5)}
    missing_reasons["B0"] = {"subscribed_no_tick": 1}
    tick_snaps = {f"A{i}": {"received": 0, "quality_reject": 0, "dispatched": 0,
                            "recorded_at": 1.0} for i in range(5)}
    tick_snaps["B0"] = {"received": 9, "quality_reject": 9, "dispatched": 0, "recorded_at": 1.0}
    report = compute_no_tick_report(missing_reasons, tick_snaps)
    # a1 5건 > a2 1건 → verdict a1 (KIS 미전송 가설 우세)
    assert report["summary"]["verdict"] == A1


def test_compute_report_empty_is_inconclusive():
    report = compute_no_tick_report({}, {})
    assert report["summary"]["total_no_tick_codes"] == 0
    assert report["summary"]["verdict"] == "inconclusive"


# ── markdown / main (end-to-end) ──────────────────────────────────────────────

def test_format_markdown_contains_verdict_and_table():
    report = compute_no_tick_report(
        {"A1CODE": {"subscribed_no_tick": 3}},
        {"A1CODE": {"received": 0, "quality_reject": 0, "dispatched": 0, "recorded_at": 1.0}},
    )
    md = format_markdown_report(report)
    assert "무틱(no-tick) 진단 리포트" in md
    assert A1 in md
    assert "A1CODE" in md


def test_main_writes_json_report(tmp_path, capsys):
    streaming = tmp_path / "logs" / "streaming"
    streaming.mkdir(parents=True)
    _write_jsonl(streaming / "1_streaming.log.json", [
        _streaming_line("missing_reason", "A1CODE", "subscribed_no_tick"),
    ])
    shadow_dir = tmp_path / "shadow"
    _write_jsonl(shadow_dir / "20260619.jsonl", [
        _shadow_status(1000.0, {"A1CODE": {"received": 0, "quality_reject": 0, "dispatched": 0}}),
    ])
    out_json = tmp_path / "out" / "report.json"
    rc = main([
        "--streaming-glob", str(streaming / "*_streaming.log.json"),
        "--shadow-dir", str(shadow_dir),
        "--date-from", "20260619", "--date-to", "20260619",
        "--output-json", str(out_json),
    ])
    assert rc == 0
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["summary"]["verdict"] == A1
    assert report["summary"]["classification_counts"][A1] == 1
