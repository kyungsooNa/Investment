"""No-tick 운영 실험 플랜 생성기 테스트."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.build_no_tick_operational_plan import (
    build_operational_plan,
    format_markdown_plan,
    infer_instrument_type,
    load_stock_meta,
    main,
)


def _report() -> dict:
    return {
        "summary": {
            "total_no_tick_codes": 5,
            "classification_counts": {"a1_kis_no_send": 4, "unknown_no_snapshot": 1},
            "verdict": "a1_kis_no_send",
        },
        "per_code": [
            {
                "code": "NO001",
                "classification": "a1_kis_no_send",
                "subscribed_no_tick_log_count": 50,
                "received": 0,
                "dispatched": 0,
            },
            {
                "code": "NO002",
                "classification": "a1_kis_no_send",
                "subscribed_no_tick_log_count": 40,
                "received": 0,
                "dispatched": 0,
            },
            {
                "code": "ETF01",
                "classification": "a1_kis_no_send",
                "subscribed_no_tick_log_count": 30,
                "received": 0,
                "dispatched": 0,
            },
            {
                "code": "PREF5",
                "classification": "a1_kis_no_send",
                "subscribed_no_tick_log_count": 20,
                "received": 0,
                "dispatched": 0,
            },
            {
                "code": "MISS1",
                "classification": "unknown_no_snapshot",
                "subscribed_no_tick_log_count": 2,
            },
        ],
    }


def _tick_snaps() -> dict:
    return {
        "OK001": {"received": 1000, "dispatched": 1000, "quality_reject": 0},
        "OK002": {"received": 500, "dispatched": 500, "quality_reject": 0},
        "NO001": {"received": 0, "dispatched": 0, "quality_reject": 0},
        "NO002": {"received": 0, "dispatched": 0, "quality_reject": 0},
        "ETF01": {"received": 0, "dispatched": 0, "quality_reject": 0},
        "PREF5": {"received": 0, "dispatched": 0, "quality_reject": 0},
    }


def _meta() -> dict:
    return {
        "OK001": {"name": "정상보통주A", "market": "KOSPI"},
        "OK002": {"name": "정상보통주B", "market": "KOSDAQ"},
        "NO001": {"name": "무틱보통주A", "market": "KOSPI"},
        "NO002": {"name": "무틱보통주B", "market": "KOSDAQ"},
        "ETF01": {"name": "KODEX 테스트", "market": "ETF"},
        "PREF5": {"name": "삼성전기우", "market": "KOSPI"},
        "MISS1": {"name": "스냅샷없음", "market": "KOSDAQ"},
    }


def _actions() -> dict:
    return {
        "NO001": {"missing_reason": 50, "price_subscribe": 10, "price_unsubscribe": 9},
        "NO002": {"missing_reason": 40, "price_subscribe": 8, "price_unsubscribe": 8},
        "ETF01": {"missing_reason": 30, "price_subscribe": 7, "price_unsubscribe": 7},
        "PREF5": {"missing_reason": 20, "price_subscribe": 6, "price_unsubscribe": 6},
        "OK001": {"missing_reason": 0, "subscribe": 1},
        "OK002": {"missing_reason": 0, "subscribe": 1},
    }


def test_infer_instrument_type_prefers_market_etf_and_name_preferred():
    assert infer_instrument_type("069500", "KODEX 200", "ETF") == "ETF"
    assert infer_instrument_type("009155", "삼성전기우", "KOSPI") == "preferred"
    assert infer_instrument_type("005930", "삼성전자", "KOSPI") == "common_or_other"


def test_build_operational_plan_splits_experiment_cohorts():
    plan = build_operational_plan(
        no_tick_report=_report(),
        tick_snaps=_tick_snaps(),
        stock_meta=_meta(),
        stream_actions=_actions(),
    )

    assert plan["summary"]["verdict"] == "a1_kis_no_send"
    assert plan["summary"]["by_instrument_type"]["ETF"]["no_tick"] == 1
    assert plan["summary"]["by_instrument_type"]["preferred"]["no_tick"] == 1
    assert plan["summary"]["by_instrument_type"]["common_or_other"]["received"] == 2

    experiments = {item["id"]: item for item in plan["experiments"]}
    assert experiments["A_common_stock_only"]["codes"] == ["OK001", "OK002", "NO001", "NO002"]
    assert experiments["B_non_common_only"]["codes"] == ["ETF01", "PREF5"]
    assert experiments["C_no_tick_common_solo"]["codes"] == ["NO001", "NO002", "MISS1"]
    assert experiments["D_refresh_observation"]["codes"][:2] == ["NO001", "NO002"]


def test_format_markdown_plan_contains_kis_summary_and_experiments():
    plan = build_operational_plan(
        no_tick_report=_report(),
        tick_snaps=_tick_snaps(),
        stock_meta=_meta(),
        stream_actions=_actions(),
    )

    md = format_markdown_plan(plan)

    assert "No-Tick Operational Experiment Plan" in md
    assert "A_common_stock_only" in md
    assert "KIS 문의 요약" in md
    assert "NO001" in md


def test_load_stock_meta_accepts_utf8_sig_csv(tmp_path):
    path = tmp_path / "stock_code_list.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["종목코드", "종목명", "시장구분"])
        writer.writeheader()
        writer.writerow({"종목코드": "005930", "종목명": "삼성전자", "시장구분": "KOSPI"})

    assert load_stock_meta(path)["005930"] == {"name": "삼성전자", "market": "KOSPI"}


def test_main_writes_json_and_markdown(tmp_path):
    report = tmp_path / "no_tick.json"
    report.write_text(json.dumps(_report(), ensure_ascii=False), encoding="utf-8")
    stock_meta = tmp_path / "stock_code_list.csv"
    with stock_meta.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["종목코드", "종목명", "시장구분"])
        writer.writeheader()
        for code, item in _meta().items():
            writer.writerow({"종목코드": code, "종목명": item["name"], "시장구분": item["market"]})

    out_json = tmp_path / "plan.json"
    out_md = tmp_path / "plan.md"

    rc = main([
        "--no-tick-report", str(report),
        "--stock-meta", str(stock_meta),
        "--output-json", str(out_json),
        "--output-markdown", str(out_md),
    ])

    assert rc == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["experiments"][0]["id"] == "A_common_stock_only"
    assert "No-Tick Operational Experiment Plan" in out_md.read_text(encoding="utf-8")
