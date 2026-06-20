"""No-tick 운영 실험 결과 분석기 테스트."""
from __future__ import annotations

import json

from scripts.analyze_no_tick_operational_experiment import (
    analyze_result,
    analyze_results,
    classify_code_result,
    format_markdown_analysis,
    main,
)


def _result(experiment_id: str, per_code: dict) -> dict:
    received = [code for code, row in per_code.items() if row.get("received_delta", 0) > 0]
    no_tick = [code for code, row in per_code.items() if row.get("received_delta", 0) <= 0]
    subscribe_failures = [code for code, row in per_code.items() if row.get("subscribe_ok") is False]
    ack_failures = [code for code, row in per_code.items() if row.get("ack_ok") is False]
    return {
        "status": "completed",
        "experiment_id": experiment_id,
        "duration_sec": 180,
        "summary": {
            "total_codes": len(per_code),
            "received_codes": len(received),
            "no_tick_codes": len(no_tick),
            "received_code_list": received,
            "no_tick_code_list": no_tick,
            "subscribe_failures": subscribe_failures,
            "ack_failures": ack_failures,
        },
        "per_code": per_code,
    }


def test_classify_code_result_prioritizes_transport_failures():
    assert classify_code_result({"subscribe_ok": False, "ack_ok": False, "received_delta": 0}) == "subscribe_failure"
    assert classify_code_result({"subscribe_ok": True, "ack_ok": False, "received_delta": 0}) == "ack_failure"
    assert classify_code_result({"subscribe_ok": True, "ack_ok": True, "quality_reject_delta": 2}) == "quality_reject"
    assert classify_code_result({"subscribe_ok": True, "ack_ok": True, "received_delta": 5}) == "received"
    assert classify_code_result({"subscribe_ok": True, "ack_ok": True, "received_delta": 0}) == "no_tick"


def test_analyze_result_a_common_stock_persists_when_no_tick_common_remains():
    result = _result(
        "A_common_stock_only",
        {
            "OK001": {"name": "정상", "instrument_type": "common_or_other", "subscribe_ok": True, "ack_ok": True, "received_delta": 10, "quality_reject_delta": 0},
            "NO001": {"name": "무틱", "instrument_type": "common_or_other", "subscribe_ok": True, "ack_ok": True, "received_delta": 0, "quality_reject_delta": 0},
        },
    )

    analysis = analyze_result(result)

    assert analysis["verdict"] == "common_no_tick_persists"
    assert analysis["counts"]["received"] == 1
    assert analysis["counts"]["no_tick"] == 1
    assert analysis["next_action"] == "Run C_no_tick_common_solo or inspect symbol/slot ordering."


def test_analyze_result_b_non_common_all_no_tick_is_product_class_signal():
    result = _result(
        "B_non_common_only",
        {
            "ETF01": {"instrument_type": "ETF", "subscribe_ok": True, "ack_ok": True, "received_delta": 0, "quality_reject_delta": 0},
            "PREF5": {"instrument_type": "preferred", "subscribe_ok": True, "ack_ok": True, "received_delta": 0, "quality_reject_delta": 0},
        },
    )

    analysis = analyze_result(result)

    assert analysis["verdict"] == "non_common_product_class_no_tick_likely"
    assert "KIS" in analysis["evidence"]


def test_analyze_result_c_solo_received_points_to_slot_or_context():
    result = _result(
        "C_no_tick_common_solo",
        {
            "NO001": {"instrument_type": "common_or_other", "subscribe_ok": True, "ack_ok": True, "received_delta": 12, "quality_reject_delta": 0},
            "NO002": {"instrument_type": "common_or_other", "subscribe_ok": True, "ack_ok": True, "received_delta": 0, "quality_reject_delta": 0},
        },
    )

    analysis = analyze_result(result)

    assert analysis["verdict"] == "slot_or_context_effect_likely"
    assert analysis["counts"]["received"] == 1


def test_analyze_result_d_refresh_all_no_tick_marks_ineffective():
    result = _result(
        "D_refresh_observation",
        {
            "NO001": {"subscribe_ok": True, "ack_ok": True, "received_delta": 0, "quality_reject_delta": 0},
        },
    )

    assert analyze_result(result)["verdict"] == "refresh_ineffective"


def test_analyze_results_and_markdown_summarize_multiple_experiments():
    analysis = analyze_results([
        _result("A_common_stock_only", {"OK001": {"subscribe_ok": True, "ack_ok": True, "received_delta": 5}}),
        _result("B_non_common_only", {"ETF01": {"subscribe_ok": True, "ack_ok": True, "received_delta": 0}}),
    ])

    md = format_markdown_analysis(analysis)

    assert analysis["summary"]["experiments"] == 2
    assert analysis["summary"]["verdicts"]["all_received"] == 1
    assert "B_non_common_only" in md
    assert "non_common_product_class_no_tick_likely" in md


def test_main_writes_json_and_markdown(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(_result("A_common_stock_only", {"OK001": {"subscribe_ok": True, "ack_ok": True, "received_delta": 5}}), ensure_ascii=False),
        encoding="utf-8",
    )
    out_json = tmp_path / "analysis.json"
    out_md = tmp_path / "analysis.md"

    rc = main([
        "--result", str(result_path),
        "--output-json", str(out_json),
        "--output-markdown", str(out_md),
    ])

    assert rc == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["summary"]["experiments"] == 1
    assert "No-Tick Operational Experiment Analysis" in out_md.read_text(encoding="utf-8")
