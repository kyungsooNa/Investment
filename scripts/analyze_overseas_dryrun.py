"""CLI: 해외 VBO dry-run shadow would-be 성과 분석 (해외 Phase 5 선행).

`OverseasVBODryRunService` 가 `signal_source="overseas_dryrun"` 로 남긴 신호는
실주문 없는 "만약 진입했다면" 가정 신호다. 각 신호엔 same-day exit 결과
(`exit_price`/`exit_reason`/`realized_pct`)가 동봉돼 있어, 별도 SELL 매칭 없이
신호 자체만으로 would-be 성과를 집계할 수 있다(국내 parity 분석과 다른 점).

집계 지표:
  - totals       : 신호수 / 승·패 / win_rate / 평균·중앙·합계 realized_pct
  - by_exit_reason : stop vs eod 청산 분포
  - by_exchange  : 거래소별 신호수·합계 realized_pct
  - by_date      : 거래일별 신호수·합계 realized_pct
  - sizing       : 사이징 주입 신호의 would-be USD 노출(notional) 합/평균

용도: dry-run 5거래일 누적 후 canary go/no-go 판단 데이터. 실주문 경로 없음.

Examples:
    python scripts/analyze_overseas_dryrun.py \
        --date-from 20260601 --date-to 20260605 \
        --output-json reports/overseas_dryrun.json \
        --output-markdown reports/overseas_dryrun.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SIGNAL_SOURCE = "overseas_dryrun"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_dryrun_records(
    shadow_dir: Path | str,
    date_from: str,
    date_to: str,
    signal_source: str = _SIGNAL_SOURCE,
) -> List[Dict[str, Any]]:
    """`<shadow_dir>/YYYYMMDD.jsonl` 들을 스캔해 dry-run 신호를 정규화 dict 로 반환.

    파일 선택은 파일명(flush 날짜) 기준 [date_from, date_to]. 그룹/리포트의
    trade_date 는 신호의 일봉 `date`(없으면 파일명 stem)를 사용한다.
    """
    shadow_dir = Path(shadow_dir)
    if not shadow_dir.exists():
        return []

    out: List[Dict[str, Any]] = []
    for path in sorted(shadow_dir.glob("*.jsonl")):
        stem = path.stem
        if not (len(stem) == 8 and stem.isdigit()):
            continue
        if not (date_from <= stem <= date_to):
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (raw.get("signal_source") or "") != signal_source:
                    continue
                signal = raw.get("signal") or {}
                snapshot = raw.get("snapshot") or {}
                code = raw.get("code") or signal.get("code") or ""
                realized = _to_float(signal.get("realized_pct"))
                if not code or realized is None:
                    continue
                out.append({
                    "code": code,
                    "exchange": snapshot.get("exchange") or "",
                    "trade_date": str(signal.get("date") or stem),
                    "entry_price": _to_float(signal.get("entry_price")),
                    "exit_price": _to_float(signal.get("exit_price")),
                    "exit_reason": signal.get("exit_reason") or "",
                    "realized_pct": realized,
                    "qty": signal.get("qty"),
                    "notional_usd": _to_float(signal.get("notional_usd")),
                    "krw_exposure": _to_float(signal.get("krw_exposure")),
                })
    return out


def _stats(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"avg": None, "median": None, "sum": None, "min": None, "max": None}
    return {
        "avg": sum(values) / len(values),
        "median": statistics.median(values),
        "sum": sum(values),
        "min": min(values),
        "max": max(values),
    }


def compute_dryrun_report(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [r["realized_pct"] for r in records if r.get("realized_pct") is not None]
    wins = sum(1 for v in returns if v > 0)
    losses = sum(1 for v in returns if v < 0)
    rstats = _stats(returns)

    by_exit_reason: Dict[str, int] = {}
    by_exchange: Dict[str, Dict[str, Any]] = {}
    by_date: Dict[str, Dict[str, Any]] = {}
    for r in records:
        reason = r.get("exit_reason") or "unknown"
        by_exit_reason[reason] = by_exit_reason.get(reason, 0) + 1

        ex = r.get("exchange") or "unknown"
        ex_b = by_exchange.setdefault(ex, {"signals": 0, "wins": 0, "sum_realized_pct": 0.0})
        ex_b["signals"] += 1
        ex_b["wins"] += 1 if r["realized_pct"] > 0 else 0
        ex_b["sum_realized_pct"] += r["realized_pct"]

        d = r.get("trade_date") or "unknown"
        d_b = by_date.setdefault(d, {"signals": 0, "wins": 0, "sum_realized_pct": 0.0})
        d_b["signals"] += 1
        d_b["wins"] += 1 if r["realized_pct"] > 0 else 0
        d_b["sum_realized_pct"] += r["realized_pct"]

    notionals = [r["notional_usd"] for r in records if r.get("notional_usd") is not None]
    krw_exposures = [r["krw_exposure"] for r in records if r.get("krw_exposure") is not None]
    sizing = {
        "sized_count": len(notionals),
        "total_notional_usd": round(sum(notionals), 4) if notionals else 0.0,
        "avg_notional_usd": round(sum(notionals) / len(notionals), 4) if notionals else None,
        "fx_sized_count": len(krw_exposures),
        "total_krw_exposure": round(sum(krw_exposures), 2) if krw_exposures else 0.0,
        "avg_krw_exposure": round(sum(krw_exposures) / len(krw_exposures), 2) if krw_exposures else None,
    }

    return {
        "totals": {
            "signals": len(records),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / len(returns)) if returns else None,
            "avg_realized_pct": rstats["avg"],
            "median_realized_pct": rstats["median"],
            "sum_realized_pct": rstats["sum"],
            "min_realized_pct": rstats["min"],
            "max_realized_pct": rstats["max"],
        },
        "by_exit_reason": by_exit_reason,
        "by_exchange": dict(sorted(by_exchange.items())),
        "by_date": dict(sorted(by_date.items())),
        "sizing": sizing,
    }


def format_markdown_report(report: Dict[str, Any]) -> str:
    cfg = report.get("config", {})
    t = report["totals"]
    lines: List[str] = []
    lines.append("# Overseas VBO Dry-run Would-be Performance")
    lines.append("")
    if cfg:
        lines.append(
            f"- date: `{cfg.get('date_from', '')} ~ {cfg.get('date_to', '')}`  "
            f"signal_source: `{cfg.get('signal_source', '')}`  "
            f"shadow_dir: `{cfg.get('shadow_dir', '')}`"
        )
        lines.append("")

    def _fmt(v: Optional[float], suffix: str = "") -> str:
        return f"{v:.3f}{suffix}" if isinstance(v, (int, float)) else "—"

    lines.append("## 전체 집계")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---:|")
    lines.append(f"| signals | {t['signals']} |")
    lines.append(f"| wins / losses | {t['wins']} / {t['losses']} |")
    lines.append(f"| win_rate | {_fmt(t['win_rate'])} |")
    lines.append(f"| avg_realized_pct | {_fmt(t['avg_realized_pct'], '%')} |")
    lines.append(f"| median_realized_pct | {_fmt(t['median_realized_pct'], '%')} |")
    lines.append(f"| sum_realized_pct | {_fmt(t['sum_realized_pct'], '%')} |")
    lines.append("")

    lines.append("## 청산 사유")
    lines.append("")
    lines.append("| reason | 건수 |")
    lines.append("|---|---:|")
    for reason, n in sorted(report.get("by_exit_reason", {}).items()):
        lines.append(f"| {reason} | {n} |")
    lines.append("")

    lines.append("## 거래소별")
    lines.append("")
    lines.append("| exchange | signals | wins | sum_realized_pct |")
    lines.append("|---|---:|---:|---:|")
    for ex, b in report.get("by_exchange", {}).items():
        lines.append(f"| {ex} | {b['signals']} | {b['wins']} | {_fmt(b['sum_realized_pct'], '%')} |")
    lines.append("")

    by_date = report.get("by_date", {})
    if by_date:
        lines.append("## 거래일별")
        lines.append("")
        lines.append("| date | signals | wins | sum_realized_pct |")
        lines.append("|---|---:|---:|---:|")
        for d, b in by_date.items():
            lines.append(f"| {d} | {b['signals']} | {b['wins']} | {_fmt(b['sum_realized_pct'], '%')} |")
        lines.append("")

    sz = report.get("sizing", {})
    lines.append("## 사이징 (would-be USD 노출)")
    lines.append("")
    lines.append(f"- sized_count: {sz.get('sized_count', 0)}")
    lines.append(f"- total_notional_usd: {_fmt(sz.get('total_notional_usd'))}")
    lines.append(f"- avg_notional_usd: {_fmt(sz.get('avg_notional_usd'))}")
    lines.append(f"- fx_sized_count: {sz.get('fx_sized_count', 0)}")
    lines.append(f"- total_krw_exposure: {_fmt(sz.get('total_krw_exposure'))}")
    lines.append(f"- avg_krw_exposure: {_fmt(sz.get('avg_krw_exposure'))}")
    lines.append("")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="해외 VBO dry-run shadow would-be 성과 분석",
    )
    parser.add_argument("--shadow-dir", default="logs/strategies/event_shadow")
    parser.add_argument("--date-from", required=True, help="YYYYMMDD")
    parser.add_argument("--date-to", required=True, help="YYYYMMDD")
    parser.add_argument("--signal-source", default=_SIGNAL_SOURCE)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    records = load_dryrun_records(
        shadow_dir=Path(args.shadow_dir),
        date_from=args.date_from,
        date_to=args.date_to,
        signal_source=args.signal_source,
    )
    report = compute_dryrun_report(records)
    report["config"] = {
        "date_from": args.date_from,
        "date_to": args.date_to,
        "signal_source": args.signal_source,
        "shadow_dir": str(args.shadow_dir),
    }

    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"[INFO] JSON report: {out_json}")
    if args.output_markdown:
        out_md = Path(args.output_markdown)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(format_markdown_report(report), encoding="utf-8")
        print(f"[INFO] Markdown report: {out_md}")

    if not (args.output_json or args.output_markdown):
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
