"""Analyze no-tick operational experiment result JSON files.

The runner records raw subscription and tick-ingest deltas. This analyzer turns
those results into experiment-level verdicts that can guide the next market
session without manually comparing per-code tables.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CODE_CLASSES = (
    "subscribe_failure",
    "ack_failure",
    "quality_reject",
    "received",
    "no_tick",
    "not_executed",
)


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_result(path: Path) -> Dict[str, Any]:
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    result.setdefault("_source_path", str(path))
    return result


def expand_result_paths(patterns: List[str]) -> List[Path]:
    paths: List[Path] = []
    for pattern in patterns:
        matched = [Path(p) for p in glob.glob(pattern)]
        if matched:
            paths.extend(matched)
        else:
            paths.append(Path(pattern))
    return sorted(dict.fromkeys(paths))


def classify_code_result(row: Dict[str, Any]) -> str:
    if row.get("subscribe_ok") is False:
        return "subscribe_failure"
    if row.get("ack_ok") is False:
        return "ack_failure"
    if _to_int(row.get("quality_reject_delta")) > 0:
        return "quality_reject"
    if _to_int(row.get("received_delta")) > 0:
        return "received"
    if row.get("subscribe_ok") is None and row.get("ack_ok") is None:
        return "not_executed"
    return "no_tick"


def _counts(per_code_analysis: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    counter = Counter(row["classification"] for row in per_code_analysis.values())
    return {key: int(counter.get(key, 0)) for key in CODE_CLASSES}


def _has_transport_failure(counts: Dict[str, int]) -> bool:
    return counts.get("subscribe_failure", 0) > 0 or counts.get("ack_failure", 0) > 0


def _verdict_for_experiment(experiment_id: str, counts: Dict[str, int], total: int) -> tuple[str, str, str]:
    received = counts.get("received", 0)
    no_tick = counts.get("no_tick", 0)
    quality_reject = counts.get("quality_reject", 0)

    if total <= 0:
        return "empty_result", "No codes were present in the result.", "Re-run with a non-empty cohort."
    if counts.get("not_executed", 0) == total:
        return "not_executed", "Dry-run or non-live result; no tick classification was performed.", "Run with --execute-live during market hours."
    if _has_transport_failure(counts):
        return "transport_failure", "At least one subscription or ACK failed.", "Fix subscribe/ACK failures before interpreting no-tick behavior."
    if quality_reject > 0:
        return "quality_gate_interference", "Ticks arrived but were rejected by the quality gate.", "Inspect DataQuality reject metadata before KIS/feed conclusions."

    if experiment_id == "A_common_stock_only":
        if received == total:
            return "all_received", "All common-stock symbols received ticks.", "Proceed to B_non_common_only to isolate product-class behavior."
        if no_tick > 0 and received > 0:
            return "common_no_tick_persists", "Some common stocks still received zero ticks while peers received ticks.", "Run C_no_tick_common_solo or inspect symbol/slot ordering."
        if no_tick == total:
            return "all_common_no_tick", "No common-stock symbols received ticks.", "Check session-wide feed/connectivity before product-class conclusions."

    if experiment_id == "B_non_common_only":
        if no_tick == total:
            return "non_common_product_class_no_tick_likely", "KIS sent no frames for the ETF/preferred-only cohort after ACK.", "Ask KIS to confirm ETF/preferred WebSocket support or separate product TR behavior."
        if received > 0:
            return "non_common_supported_in_isolation", "At least one ETF/preferred symbol received ticks in isolation.", "Compare against mixed A/D contexts for slot or ordering effects."

    if experiment_id == "C_no_tick_common_solo":
        if received > 0:
            return "slot_or_context_effect_likely", "Previously no-tick common names received ticks when isolated.", "Reduce mixed-cohort size or vary subscription order."
        if no_tick == total:
            return "symbol_or_account_level_issue_likely", "Isolated common-stock symbols still received zero ticks.", "Escalate selected common symbols to KIS with runner output attached."

    if experiment_id == "D_refresh_observation":
        if no_tick == total:
            return "refresh_ineffective", "No refreshed symbols recovered tick flow.", "Reduce refresh churn and quarantine no-tick symbols intraday."
        if received > 0:
            return "refresh_may_help", "At least one refreshed symbol received ticks.", "Compare received timing against refresh timestamps before changing policy."

    if received == total:
        return "all_received", "All symbols received ticks.", "Continue with the next planned cohort."
    if no_tick == total:
        return "all_no_tick", "No symbols received ticks despite no transport failure.", "Check whether this is cohort-specific or session-wide."
    return "mixed_result", "Received and no-tick symbols are mixed.", "Compare per-code class, subscription order, and cohort composition."


def analyze_result(result: Dict[str, Any]) -> Dict[str, Any]:
    experiment_id = str(result.get("experiment_id") or "")
    per_code = result.get("per_code") or {}
    per_code_analysis: Dict[str, Dict[str, Any]] = {}
    for code, row in per_code.items():
        classification = classify_code_result(row)
        per_code_analysis[str(code)] = {
            "code": str(code),
            "name": row.get("name", ""),
            "instrument_type": row.get("instrument_type", ""),
            "classification": classification,
            "received_delta": _to_int(row.get("received_delta")),
            "dispatched_delta": _to_int(row.get("dispatched_delta")),
            "quality_reject_delta": _to_int(row.get("quality_reject_delta")),
            "subscribe_ok": row.get("subscribe_ok"),
            "ack_ok": row.get("ack_ok"),
        }

    counts = _counts(per_code_analysis)
    verdict, evidence, next_action = _verdict_for_experiment(experiment_id, counts, len(per_code_analysis))
    return {
        "source_path": result.get("_source_path", ""),
        "status": result.get("status", ""),
        "experiment_id": experiment_id,
        "duration_sec": result.get("duration_sec"),
        "verdict": verdict,
        "evidence": evidence,
        "next_action": next_action,
        "counts": counts,
        "per_code": per_code_analysis,
    }


def analyze_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    experiments = [analyze_result(result) for result in results]
    verdicts = Counter(item["verdict"] for item in experiments)
    code_classes = Counter()
    for item in experiments:
        code_classes.update(item.get("counts", {}))
    return {
        "summary": {
            "experiments": len(experiments),
            "verdicts": dict(sorted(verdicts.items())),
            "code_classes": {key: int(code_classes.get(key, 0)) for key in CODE_CLASSES},
        },
        "experiments": experiments,
    }


def format_markdown_analysis(analysis: Dict[str, Any]) -> str:
    summary = analysis.get("summary", {})
    lines = [
        "# No-Tick Operational Experiment Analysis",
        "",
        "## Summary",
        "",
        f"- Experiments: {summary.get('experiments', 0)}",
        "",
        "### Verdicts",
        "",
        "| Verdict | Count |",
        "|---------|------:|",
    ]
    for verdict, count in (summary.get("verdicts") or {}).items():
        lines.append(f"| {verdict} | {count} |")

    lines.extend([
        "",
        "### Code Classes",
        "",
        "| Class | Count |",
        "|-------|------:|",
    ])
    for klass, count in (summary.get("code_classes") or {}).items():
        lines.append(f"| {klass} | {count} |")

    lines.extend(["", "## Experiments", ""])
    for item in analysis.get("experiments", []):
        lines.extend([
            f"### {item.get('experiment_id', '')}",
            "",
            f"- Verdict: `{item.get('verdict', '')}`",
            f"- Evidence: {item.get('evidence', '')}",
            f"- Next action: {item.get('next_action', '')}",
            "",
            "| Code | Name | Type | Class | Received Δ | Dispatched Δ | Reject Δ |",
            "|------|------|------|-------|-----------:|-------------:|---------:|",
        ])
        for code, row in item.get("per_code", {}).items():
            lines.append(
                f"| {code} | {row.get('name', '')} | {row.get('instrument_type', '')} | "
                f"{row.get('classification', '')} | {row.get('received_delta', 0)} | "
                f"{row.get('dispatched_delta', 0)} | {row.get('quality_reject_delta', 0)} |"
            )
        lines.append("")
    return "\n".join(lines)


def _write_outputs(analysis: Dict[str, Any], output_json: Optional[Path], output_markdown: Optional[Path]) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON analysis: {output_json}")
    if output_markdown:
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(format_markdown_analysis(analysis), encoding="utf-8")
        print(f"[INFO] Markdown analysis: {output_markdown}")
    if not (output_json or output_markdown):
        print(json.dumps(analysis, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="append", required=True, help="Result JSON path or glob. Repeatable.")
    parser.add_argument("--output-json", type=Path, help="Analysis JSON path.")
    parser.add_argument("--output-markdown", type=Path, help="Analysis Markdown path.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    paths = expand_result_paths(args.result)
    results = [load_result(path) for path in paths]
    analysis = analyze_results(results)
    _write_outputs(analysis, args.output_json, args.output_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
