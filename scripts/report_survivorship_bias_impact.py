"""Report trade PnL tied to survivorship-bias universe gaps."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional


def _as_code(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_sold(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").upper()
    if status:
        return status == "SOLD"
    action = str(record.get("action") or record.get("side") or "").upper()
    return action == "SELL"


def _net_pnl(record: dict[str, Any]) -> float:
    for key in ("net_pnl", "realized_net_pnl", "pnl", "profit"):
        if key in record:
            return _as_float(record.get(key))
    return 0.0


def _trade_code(record: dict[str, Any]) -> str:
    return _as_code(record.get("code") or record.get("symbol") or record.get("stock_code"))


def _code_set(rows: list[dict[str, Any]]) -> set[str]:
    return {_as_code(row.get("symbol") or row.get("code")) for row in rows}


def _summarize_codes(
    records: list[dict[str, Any]],
    codes: set[str],
) -> dict[str, Any]:
    by_code: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"code": "", "trade_count": 0, "net_pnl": 0.0}
    )
    total_pnl = 0.0
    trade_count = 0
    for record in records:
        code = _trade_code(record)
        if code not in codes:
            continue
        pnl = _net_pnl(record)
        trade_count += 1
        total_pnl += pnl
        row = by_code[code]
        row["code"] = code
        row["trade_count"] += 1
        row["net_pnl"] += pnl
    by_code_rows = sorted(by_code.values(), key=lambda r: (r["net_pnl"], r["code"]))
    return {
        "trade_count": trade_count,
        "net_pnl": total_pnl,
        "by_code": by_code_rows,
    }


def compute_impact_summary(
    universe_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
) -> dict[str, Any]:
    gap = universe_payload.get("survivorship_gap") or {}
    sold_records = [
        record for record in (backtest_payload.get("journal_records") or [])
        if isinstance(record, dict) and _is_sold(record)
    ]
    delisted_only_codes = _code_set(gap.get("delisted_only") or [])
    current_only_codes = _code_set(gap.get("current_only") or [])
    total_pnl = sum(_net_pnl(record) for record in sold_records)
    return {
        "totals": {
            "sold_trade_count": len(sold_records),
            "sold_net_pnl": total_pnl,
        },
        "delisted_only": _summarize_codes(sold_records, delisted_only_codes),
        "current_only": _summarize_codes(sold_records, current_only_codes),
    }


def format_markdown_report(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    delisted = summary["delisted_only"]
    current = summary["current_only"]
    lines = [
        "# Survivorship Bias Impact",
        "",
        "## Summary",
        "",
        f"- sold trades: {totals['sold_trade_count']}",
        f"- sold net pnl: {totals['sold_net_pnl']:,.0f}",
        f"- delisted-only trades: {delisted['trade_count']}",
        f"- delisted-only net pnl: {delisted['net_pnl']:,.0f}",
        f"- current-only trades: {current['trade_count']}",
        f"- current-only net pnl: {current['net_pnl']:,.0f}",
        "",
        "## Delisted-Only By Code",
        "",
    ]
    if not delisted["by_code"]:
        lines.append("- None")
    for row in delisted["by_code"]:
        lines.append(
            f"- `{row['code']}` trades={row['trade_count']} net_pnl={row['net_pnl']:,.0f}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize backtest PnL for survivorship-bias universe gaps.",
    )
    parser.add_argument("--universe-json", required=True)
    parser.add_argument("--backtest-json", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    summary = compute_impact_summary(
        _read_json(args.universe_json),
        _read_json(args.backtest_json),
    )
    wrote = False
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] JSON impact report: {path}")
        wrote = True
    if args.output_markdown:
        path = Path(args.output_markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_markdown_report(summary), encoding="utf-8")
        print(f"[INFO] Markdown impact report: {path}")
        wrote = True
    if not wrote:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
