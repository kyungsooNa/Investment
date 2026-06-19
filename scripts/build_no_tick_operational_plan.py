"""Build an operations-ready experiment plan from no-tick diagnosis artifacts.

The no-tick classifier answers "what happened?". This script turns that answer
into small live-session cohorts that can separate product-class, slot/order, and
symbol-level WebSocket feed issues.
"""
from __future__ import annotations

import argparse
import csv
import glob as _glob
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_no_tick_diagnosis import (
    DEFAULT_STREAMING_GLOB,
    filter_streaming_paths_by_date,
    load_tick_ingest_snapshots,
)


ETF_NAME_HINTS = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "SOL",
    "PLUS",
    "KOSEF",
    "KBSTAR",
    "HANARO",
    "TIMEFOLIO",
    "히어로즈",
    "ARIRANG",
)


def infer_instrument_type(code: str, name: str, market: str = "") -> str:
    """Classify a Korean listing into coarse operational buckets."""
    code = str(code or "").strip()
    name = str(name or "").strip()
    market = str(market or "").strip().upper()
    if market == "ETF" or any(hint in name for hint in ETF_NAME_HINTS):
        return "ETF"
    if "ETN" in name:
        return "ETN"
    if name.endswith("우") or re.search(r"우[B-C]?$", name):
        return "preferred"
    if not market and re.search(r"[A-Z]", code):
        return "non_numeric_code"
    return "common_or_other"


def load_stock_meta(path: Path) -> Dict[str, Dict[str, str]]:
    """Load `data/stock_code_list.csv` style metadata."""
    out: Dict[str, Dict[str, str]] = {}
    path = Path(path)
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = str(row.get("종목코드") or "").strip()
            if not code:
                continue
            out[code] = {
                "name": str(row.get("종목명") or ""),
                "market": str(row.get("시장구분") or ""),
            }
    return out


def load_stream_actions(paths: List[Path]) -> Dict[str, Dict[str, int]]:
    """Count streaming actions per code from JsonFormatter line logs."""
    counts: Dict[str, Counter] = {}
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                data = rec.get("data")
                if not isinstance(data, dict):
                    continue
                code = data.get("code")
                action = data.get("action")
                if not code or not action:
                    continue
                bucket = counts.setdefault(str(code), Counter())
                bucket[str(action)] += 1
    return {code: dict(counter) for code, counter in counts.items()}


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_for_code(
    code: str,
    *,
    no_tick_entry: Optional[Dict[str, Any]],
    tick_snap: Optional[Dict[str, Any]],
    stock_meta: Dict[str, Dict[str, str]],
    stream_actions: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    meta = stock_meta.get(code, {})
    name = meta.get("name", "")
    market = meta.get("market", "")
    received = _to_int((tick_snap or {}).get("received"))
    dispatched = _to_int((tick_snap or {}).get("dispatched"))
    classification = (
        str(no_tick_entry.get("classification"))
        if no_tick_entry is not None
        else ("received" if received > 0 else "zero_snapshot")
    )
    actions = stream_actions.get(code, {})
    no_tick_logs = (
        _to_int(no_tick_entry.get("subscribed_no_tick_log_count"))
        if no_tick_entry is not None
        else _to_int(actions.get("missing_reason"))
    )
    return {
        "code": code,
        "name": name,
        "market": market,
        "instrument_type": infer_instrument_type(code, name, market),
        "classification": classification,
        "received": received if tick_snap is not None else None,
        "dispatched": dispatched if tick_snap is not None else None,
        "no_tick_logs": no_tick_logs,
        "missing_reason": _to_int(actions.get("missing_reason")),
        "subscribe": _to_int(actions.get("subscribe")),
        "price_subscribe": _to_int(actions.get("price_subscribe")),
        "price_unsubscribe": _to_int(actions.get("price_unsubscribe")),
    }


def _sort_received(row: Dict[str, Any]) -> tuple:
    return (-_to_int(row.get("received")), row.get("code", ""))


def _sort_no_tick(row: Dict[str, Any]) -> tuple:
    return (-_to_int(row.get("no_tick_logs")), row.get("code", ""))


def _sort_refresh(row: Dict[str, Any]) -> tuple:
    return (-_to_int(row.get("price_subscribe")), -_to_int(row.get("no_tick_logs")), row.get("code", ""))


def _compact_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("code", "name", "market", "instrument_type", "classification", "received", "no_tick_logs")
    return [{k: row.get(k) for k in keys} for row in rows]


def build_operational_plan(
    *,
    no_tick_report: Dict[str, Any],
    tick_snaps: Optional[Dict[str, Dict[str, Any]]] = None,
    stock_meta: Optional[Dict[str, Dict[str, str]]] = None,
    stream_actions: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Build cohort suggestions for the next live no-tick experiment."""
    tick_snaps = tick_snaps or {}
    stock_meta = stock_meta or {}
    stream_actions = stream_actions or {}
    no_tick_by_code = {
        str(entry.get("code")): entry
        for entry in no_tick_report.get("per_code", [])
        if entry.get("code")
    }
    all_codes = sorted(set(tick_snaps) | set(no_tick_by_code))
    rows = [
        _row_for_code(
            code,
            no_tick_entry=no_tick_by_code.get(code),
            tick_snap=tick_snaps.get(code),
            stock_meta=stock_meta,
            stream_actions=stream_actions,
        )
        for code in all_codes
    ]

    received_common = sorted(
        [
            row for row in rows
            if row["classification"] == "received"
            and row["instrument_type"] == "common_or_other"
        ],
        key=_sort_received,
    )
    no_tick_common = sorted(
        [
            row for row in rows
            if row["code"] in no_tick_by_code
            and row["instrument_type"] == "common_or_other"
        ],
        key=_sort_no_tick,
    )
    observed_no_tick_common = [row for row in no_tick_common if row.get("received") is not None]
    no_tick_non_common = sorted(
        [
            row for row in rows
            if row["code"] in no_tick_by_code
            and row["instrument_type"] != "common_or_other"
        ],
        key=lambda r: (r["instrument_type"] != "ETF", _sort_no_tick(r)),
    )
    refresh_targets = sorted(
        [row for row in rows if row["code"] in no_tick_by_code],
        key=_sort_refresh,
    )

    by_type: Dict[str, Dict[str, int]] = {}
    for row in rows:
        bucket = by_type.setdefault(row["instrument_type"], {"received": 0, "no_tick": 0, "total": 0})
        bucket["total"] += 1
        if row["code"] in no_tick_by_code:
            bucket["no_tick"] += 1
        if row["classification"] == "received":
            bucket["received"] += 1

    experiments = [
        {
            "id": "A_common_stock_only",
            "goal": "Remove ETF/preferred contamination and test whether common-stock no-tick persists.",
            "codes": [r["code"] for r in received_common[:5] + observed_no_tick_common[:5]],
            "expected_signal": "If no-tick common names recover, mixed product class or slot churn is implicated.",
            "rows": _compact_rows(received_common[:5] + observed_no_tick_common[:5]),
        },
        {
            "id": "B_non_common_only",
            "goal": "Check whether ETF/preferred symbols structurally receive no price frames.",
            "codes": [r["code"] for r in no_tick_non_common[:5]],
            "expected_signal": "If these still receive zero frames solo, KIS product-class behavior is likely.",
            "rows": _compact_rows(no_tick_non_common[:5]),
        },
        {
            "id": "C_no_tick_common_solo",
            "goal": "Isolate symbol-level vs slot/order effects for common-stock no-tick names.",
            "codes": [r["code"] for r in no_tick_common[:3]],
            "expected_signal": "If solo common names receive ticks, the 30-40 name subscription context is implicated.",
            "rows": _compact_rows(no_tick_common[:3]),
        },
        {
            "id": "D_refresh_observation",
            "goal": "Observe whether repeated subscribed_no_tick refresh ever restores frames.",
            "codes": [r["code"] for r in refresh_targets[:5]],
            "expected_signal": "If refresh still does not restore frames, reduce churn or quarantine no-tick symbols intraday.",
            "rows": _compact_rows(refresh_targets[:5]),
        },
    ]

    summary = {
        "verdict": no_tick_report.get("summary", {}).get("verdict", ""),
        "total_no_tick_codes": len(no_tick_by_code),
        "total_received_codes": len(received_common),
        "by_instrument_type": by_type,
    }
    return {
        "summary": summary,
        "experiments": experiments,
        "kis_inquiry": {
            "points": [
                "Confirm whether domestic equity price WebSocket frames are supported identically for ETF/preferred symbols.",
                "Subscriptions reached ACK/active state, but selected symbols remained received=0 for the full session.",
                "Some common stocks received thousands of frames in the same session, so the feed was not globally dead.",
                "Repeated unsubscribe/subscribe refresh did not restore frames for a1_kis_no_send symbols.",
                "not_subscribed and DataQuality reject were not the dominant failure modes.",
            ],
            "attachments": [
                "reports/no_tick_diagnosis_20260619.md",
                "reports/no_tick_diagnosis_20260619.json",
                "reports/no_tick_operational_diagnosis_20260619.md",
            ],
        },
        "rows": rows,
    }


def format_markdown_plan(plan: Dict[str, Any]) -> str:
    summary = plan.get("summary", {})
    lines: List[str] = [
        "# No-Tick Operational Experiment Plan",
        "",
        "## Summary",
        "",
        f"- Verdict: `{summary.get('verdict', '')}`",
        f"- No-tick codes: {summary.get('total_no_tick_codes', 0)}",
        f"- Received common-stock codes: {summary.get('total_received_codes', 0)}",
        "",
        "### By Instrument Type",
        "",
        "| Type | Total | No-tick | Received |",
        "|------|------:|--------:|---------:|",
    ]
    for typ, counts in sorted((summary.get("by_instrument_type") or {}).items()):
        lines.append(
            f"| {typ} | {counts.get('total', 0)} | {counts.get('no_tick', 0)} | "
            f"{counts.get('received', 0)} |"
        )

    lines.extend(["", "## Experiments", ""])
    for exp in plan.get("experiments", []):
        lines.extend([
            f"### {exp.get('id', '')}",
            "",
            f"- Goal: {exp.get('goal', '')}",
            f"- Codes: `{', '.join(exp.get('codes') or [])}`",
            f"- Expected signal: {exp.get('expected_signal', '')}",
            "",
            "| Code | Name | Market | Type | Class | Received | No-tick logs |",
            "|------|------|--------|------|-------|---------:|-------------:|",
        ])
        for row in exp.get("rows", []):
            lines.append(
                f"| {row.get('code', '')} | {row.get('name', '')} | "
                f"{row.get('market', '')} | {row.get('instrument_type', '')} | "
                f"{row.get('classification', '')} | {row.get('received', '')} | "
                f"{row.get('no_tick_logs', '')} |"
            )
        lines.append("")

    lines.extend(["## KIS 문의 요약", ""])
    for idx, point in enumerate(plan.get("kis_inquiry", {}).get("points", []), start=1):
        lines.append(f"{idx}. {point}")
    lines.extend(["", "### Attachments", ""])
    for item in plan.get("kis_inquiry", {}).get("attachments", []):
        lines.append(f"- `{item}`")
    lines.append("")
    return "\n".join(lines)


def _infer_dates_from_report(report: Dict[str, Any]) -> tuple[str, str]:
    cfg = report.get("config") or {}
    date_from = str(cfg.get("date_from") or "")
    date_to = str(cfg.get("date_to") or date_from)
    return date_from, date_to


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build no-tick operational experiment plan")
    parser.add_argument("--no-tick-report", required=True, help="JSON output from analyze_no_tick_diagnosis.py")
    parser.add_argument("--stock-meta", default="data/stock_code_list.csv")
    parser.add_argument("--shadow-dir", default="logs/strategies/event_shadow")
    parser.add_argument("--streaming-glob", default=DEFAULT_STREAMING_GLOB)
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-markdown", default="")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    report_path = Path(args.no_tick_report)
    no_tick_report = json.loads(report_path.read_text(encoding="utf-8"))
    inferred_from, inferred_to = _infer_dates_from_report(no_tick_report)
    date_from = args.date_from or inferred_from
    date_to = args.date_to or inferred_to

    tick_snaps: Dict[str, Dict[str, Any]] = {}
    if date_from and date_to:
        tick_snaps = load_tick_ingest_snapshots(Path(args.shadow_dir), date_from, date_to)

    streaming_paths = [Path(p) for p in sorted(_glob.glob(args.streaming_glob))]
    if date_from and date_to:
        streaming_paths = filter_streaming_paths_by_date(streaming_paths, date_from, date_to)
    stream_actions = load_stream_actions(streaming_paths)

    plan = build_operational_plan(
        no_tick_report=no_tick_report,
        tick_snaps=tick_snaps,
        stock_meta=load_stock_meta(Path(args.stock_meta)),
        stream_actions=stream_actions,
    )

    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON plan: {out_json}")
    if args.output_markdown:
        out_md = Path(args.output_markdown)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(format_markdown_plan(plan), encoding="utf-8")
        print(f"[INFO] Markdown plan: {out_md}")
    if not (args.output_json or args.output_markdown):
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
