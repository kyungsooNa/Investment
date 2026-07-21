"""CLI: summarize replay microstructure capture quality.

The analyzer reads ``replay_microstructure_YYYYMMDD.json`` files produced by
``scripts.capture_backtest_microstructure`` and turns their metadata into a
small daily QC report. It does not call broker APIs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.backtest_microstructure_quality import (
    MicrostructureQualityThresholds,
    coverage_pct,
    summarize_capture_quality,
)


def load_capture_payloads(
    input_dir: Path | str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load capture payloads sorted by filename date."""
    input_dir = Path(input_dir)
    if not input_dir.exists():
        return []

    payloads: List[Dict[str, Any]] = []
    for path in sorted(input_dir.glob("replay_microstructure_*.json")):
        stem_date = path.stem.replace("replay_microstructure_", "", 1)
        if len(stem_date) != 8 or not stem_date.isdigit():
            continue
        if date_from and stem_date < date_from:
            continue
        if date_to and stem_date > date_to:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def compute_quality_report(
    payloads: List[Dict[str, Any]],
    *,
    min_intraday_coverage_pct: float = 80.0,
    min_program_overlay_coverage_pct: float = 80.0,
    min_program_db_coverage_pct: float = 50.0,
    min_execution_strength_db_coverage_pct: float = 30.0,
    min_orderbook_db_coverage_pct: float = 30.0,
    min_orderbook_rows_per_code: int = 30,
    max_stale_rows: int = 0,
) -> Dict[str, Any]:
    """Compute daily and aggregate quality metrics."""
    by_date: Dict[str, Dict[str, Any]] = {}
    thresholds = MicrostructureQualityThresholds(
        min_intraday_coverage_pct=min_intraday_coverage_pct,
        min_program_overlay_coverage_pct=min_program_overlay_coverage_pct,
        min_program_db_coverage_pct=min_program_db_coverage_pct,
        min_execution_strength_db_coverage_pct=min_execution_strength_db_coverage_pct,
        min_orderbook_db_coverage_pct=min_orderbook_db_coverage_pct,
        min_orderbook_rows_per_code=min_orderbook_rows_per_code,
        max_stale_rows=max_stale_rows,
    )
    for payload in payloads:
        daily = summarize_capture_quality(
            payload,
            thresholds=thresholds,
        )
        key = daily["trade_date"] or f"unknown_{len(by_date) + 1}"
        by_date[key] = daily

    total_codes = sum(row["codes"] for row in by_date.values())
    intraday_available = sum(row["intraday_available"] for row in by_date.values())
    execution_strength_available = sum(
        row["execution_strength_available"] for row in by_date.values()
    )
    program_overlay_available = sum(
        row["program_overlay_available"] for row in by_date.values()
    )
    program_db_denominator = sum(
        row["codes"] for row in by_date.values()
        if row["program_db_coverage_pct"] is not None
    )
    program_db_available = sum(
        row["program_db_available"] or 0 for row in by_date.values()
        if row["program_db_coverage_pct"] is not None
    )
    es_db_denominator = sum(
        row["codes"] for row in by_date.values()
        if row["execution_strength_db_coverage_pct"] is not None
    )
    es_db_available = sum(
        row["execution_strength_db_available"] or 0 for row in by_date.values()
        if row["execution_strength_db_coverage_pct"] is not None
    )
    orderbook_db_denominator = sum(
        row["codes"] for row in by_date.values()
        if row["orderbook_db_coverage_pct"] is not None
    )
    orderbook_db_available = sum(
        row["orderbook_db_available"] or 0 for row in by_date.values()
        if row["orderbook_db_coverage_pct"] is not None
    )
    stale_rows = sum(row["stale_minute_rows_dropped"] for row in by_date.values())
    fallback_count = sum(len(row["program_fallback_codes"]) for row in by_date.values())

    daily_failures = {
        date: row["issues"] for date, row in by_date.items()
        if row["issues"]
    }
    gate_passed = bool(payloads) and not daily_failures
    totals = {
        "capture_files": len(payloads),
        "trading_days": len(by_date),
        "total_codes": total_codes,
        "intraday_available": intraday_available,
        "intraday_coverage_pct": coverage_pct(intraday_available, total_codes),
        "execution_strength_available": execution_strength_available,
        "execution_strength_coverage_pct": coverage_pct(execution_strength_available, total_codes),
        "program_overlay_available": program_overlay_available,
        "program_overlay_coverage_pct": coverage_pct(program_overlay_available, total_codes),
        "program_db_available": program_db_available,
        "program_db_coverage_pct": (
            coverage_pct(program_db_available, program_db_denominator)
            if program_db_denominator > 0 else None
        ),
        "program_fallback_count": fallback_count,
        "program_fallback_pct": coverage_pct(fallback_count, total_codes),
        "execution_strength_db_available": es_db_available,
        "execution_strength_db_coverage_pct": (
            coverage_pct(es_db_available, es_db_denominator)
            if es_db_denominator > 0 else None
        ),
        "orderbook_db_available": orderbook_db_available,
        "orderbook_db_coverage_pct": (
            coverage_pct(orderbook_db_available, orderbook_db_denominator)
            if orderbook_db_denominator > 0 else None
        ),
        "stale_minute_rows_dropped": stale_rows,
        "quality_gate_passed": gate_passed,
        "daily_failures": daily_failures,
    }
    if not payloads:
        totals["daily_failures"] = {"all": ["no_capture_files"]}

    return {
        "config": {
            "min_intraday_coverage_pct": min_intraday_coverage_pct,
            "min_program_overlay_coverage_pct": min_program_overlay_coverage_pct,
            "min_program_db_coverage_pct": min_program_db_coverage_pct,
            "min_execution_strength_db_coverage_pct": min_execution_strength_db_coverage_pct,
            "min_orderbook_db_coverage_pct": min_orderbook_db_coverage_pct,
            "min_orderbook_rows_per_code": min_orderbook_rows_per_code,
            "max_stale_rows": max_stale_rows,
        },
        "totals": totals,
        "by_date": by_date,
    }


def build_quality_manifest(report: Dict[str, Any]) -> Dict[str, Any]:
    """백테스트 사용 가능 날짜와 격리할 날짜를 기계 판독 가능한 형태로 만든다."""
    dates: Dict[str, Dict[str, Any]] = {}
    for date, row in sorted((report.get("by_date") or {}).items()):
        valid = bool(row.get("quality_gate_passed"))
        dates[date] = {
            "valid_for_backtest": valid,
            "issues": list(row.get("issues") or []),
            "intraday_coverage_pct": row.get("intraday_coverage_pct"),
            "program_overlay_coverage_pct": row.get("program_overlay_coverage_pct"),
            "program_db_coverage_pct": row.get("program_db_coverage_pct"),
            "execution_strength_db_coverage_pct": row.get(
                "execution_strength_db_coverage_pct"
            ),
            "orderbook_db_coverage_pct": row.get("orderbook_db_coverage_pct"),
            "orderbook_sparse_codes": row.get("orderbook_sparse_codes") or [],
        }
    return {
        "schema_version": 1,
        "valid_dates": [date for date, row in dates.items() if row["valid_for_backtest"]],
        "invalid_dates": [date for date, row in dates.items() if not row["valid_for_backtest"]],
        "dates": dates,
    }


def _fmt_pct(value: Any) -> str:
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "-"


def format_markdown_report(report: Dict[str, Any]) -> str:
    totals = report.get("totals", {})
    lines: List[str] = [
        "# Microstructure Capture Quality",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| capture_files | {totals.get('capture_files', 0)} |",
        f"| total_codes | {totals.get('total_codes', 0)} |",
        f"| intraday_coverage | {_fmt_pct(totals.get('intraday_coverage_pct'))} |",
        f"| execution_strength_coverage | {_fmt_pct(totals.get('execution_strength_coverage_pct'))} |",
        f"| program_overlay_coverage | {_fmt_pct(totals.get('program_overlay_coverage_pct'))} |",
        f"| program_db_coverage | {_fmt_pct(totals.get('program_db_coverage_pct'))} |",
        f"| execution_strength_db_coverage | {_fmt_pct(totals.get('execution_strength_db_coverage_pct'))} |",
        f"| orderbook_db_coverage | {_fmt_pct(totals.get('orderbook_db_coverage_pct'))} |",
        f"| program_fallback_count | {totals.get('program_fallback_count', 0)} |",
        f"| stale_minute_rows_dropped | {totals.get('stale_minute_rows_dropped', 0)} |",
        f"| quality_gate_passed | {totals.get('quality_gate_passed', False)} |",
        "",
        "## By Date",
        "",
        "| date | codes | intraday | exec_strength | program | program_db | es_db | orderbook_db | stale | issues |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for date, row in sorted((report.get("by_date") or {}).items()):
        issues = ", ".join(row.get("issues") or []) or "-"
        lines.append(
            f"| {date} | {row.get('codes', 0)} | "
            f"{_fmt_pct(row.get('intraday_coverage_pct'))} | "
            f"{_fmt_pct(row.get('execution_strength_coverage_pct'))} | "
            f"{_fmt_pct(row.get('program_overlay_coverage_pct'))} | "
            f"{_fmt_pct(row.get('program_db_coverage_pct'))} | "
            f"{_fmt_pct(row.get('execution_strength_db_coverage_pct'))} | "
            f"{_fmt_pct(row.get('orderbook_db_coverage_pct'))} | "
            f"{row.get('stale_minute_rows_dropped', 0)} | {issues} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze replay microstructure capture quality.",
    )
    parser.add_argument("--input-dir", default="data/backtest_microstructure")
    parser.add_argument("--date-from", default=None, help="YYYYMMDD")
    parser.add_argument("--date-to", default=None, help="YYYYMMDD")
    parser.add_argument("--min-intraday-coverage-pct", type=float, default=80.0)
    parser.add_argument("--min-program-overlay-coverage-pct", type=float, default=80.0)
    parser.add_argument("--min-program-db-coverage-pct", type=float, default=50.0)
    parser.add_argument("--min-execution-strength-db-coverage-pct", type=float, default=30.0)
    parser.add_argument("--min-orderbook-db-coverage-pct", type=float, default=30.0)
    parser.add_argument("--min-orderbook-rows-per-code", type=int, default=30)
    parser.add_argument("--max-stale-rows", type=int, default=0)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    parser.add_argument(
        "--output-manifest",
        default=None,
        help="Write valid/invalid backtest dates as JSON.",
    )
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Return exit code 1 when the quality gate fails.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payloads = load_capture_payloads(
        args.input_dir,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    report = compute_quality_report(
        payloads,
        min_intraday_coverage_pct=args.min_intraday_coverage_pct,
        min_program_overlay_coverage_pct=args.min_program_overlay_coverage_pct,
        min_program_db_coverage_pct=args.min_program_db_coverage_pct,
        min_execution_strength_db_coverage_pct=args.min_execution_strength_db_coverage_pct,
        min_orderbook_db_coverage_pct=args.min_orderbook_db_coverage_pct,
        min_orderbook_rows_per_code=args.min_orderbook_rows_per_code,
        max_stale_rows=args.max_stale_rows,
    )
    report["config"].update({
        "input_dir": str(args.input_dir),
        "date_from": args.date_from,
        "date_to": args.date_to,
    })

    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[INFO] JSON report: {out_json}")
    if args.output_markdown:
        out_md = Path(args.output_markdown)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(format_markdown_report(report), encoding="utf-8")
        print(f"[INFO] Markdown report: {out_md}")
    if args.output_manifest:
        out_manifest = Path(args.output_manifest)
        out_manifest.parent.mkdir(parents=True, exist_ok=True)
        out_manifest.write_text(
            json.dumps(build_quality_manifest(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[INFO] Quality manifest: {out_manifest}")

    if not (args.output_json or args.output_markdown or args.output_manifest):
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.fail_on_gate and not report["totals"]["quality_gate_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
