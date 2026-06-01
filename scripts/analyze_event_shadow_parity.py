"""CLI: VBO event-driven shadow ↔ polling 신호 parity 분석.

P2 2-4 PR-2.5 선행 도구. shadow jsonl(`logs/strategies/event_shadow/YYYYMMDD.jsonl`)
과 폴링 signal_history(`data/StrategyScheduler/scheduler.db`)를 거래일 단위로
매칭해 다음 지표를 산출한다.

  - matched              : 같은 (strategy, code, trade_date) 에서 shadow + polling 모두 발생 → full gate parity
  - shadow_only          : shadow 만 발생 → fast path over-fire (execution_strength / program_buy 등을 생략한 결과)
  - polling_only         : polling 만 발생 → fast path miss (snapshot/router 가 놓친 케이스)
  - duplicates_shadow/polling : 같은 그룹키에서 2건 이상 발생한 추가 신호
  - lead_time_seconds    : matched 신호의 (shadow_ts - polling_ts) 통계 — 음수면 shadow 가 빠름

매칭 알고리즘: 그룹키 별 first-vs-first greedy nearest. 윈도우(`--match-window-sec`)
초과 시 매칭 실패로 처리하고 둘 다 *_only 로 분류한다. 두 번째 이후 신호는 모두
duplicate 로 빼놓는다 — VBO 는 `_bought_today` 가드로 동일 코드 재진입을 막으므로
실제 운영에서는 드물지만, fast path 가 가드 적용 전이라 발생 가능하다.

Examples:
    python scripts/analyze_event_shadow_parity.py \
        --date-from 20260520 --date-to 20260524 \
        --output-json reports/vbo_parity.json \
        --output-markdown reports/vbo_parity.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


_KST = ZoneInfo("Asia/Seoul")
_DEFAULT_STRATEGY = "래리윌리엄스VBO"


@dataclass
class ParityRecord:
    source: str           # "event_shadow" / "polling"
    strategy: str
    code: str
    action: str           # "BUY" / "SELL"
    ts_epoch: float
    ts_iso: str
    trade_date: str       # YYYYMMDD (KST)
    raw: Dict[str, Any] = field(default_factory=dict)


# ── Loaders ────────────────────────────────────────────────────────────────

def _epoch_to_kst(epoch: float) -> Tuple[str, str]:
    dt = datetime.fromtimestamp(epoch, tz=_KST)
    return dt.strftime("%Y%m%d"), dt.isoformat()


def _kst_str_to_epoch(s: str) -> float:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
    return dt.timestamp()


def load_shadow_records(
    shadow_dir: Path,
    date_from: str,
    date_to: str,
    strategy_filter: Optional[str] = None,
    signal_source: Optional[str] = None,
) -> List[ParityRecord]:
    """`<shadow_dir>/YYYYMMDD.jsonl` 파일들을 스캔해 ParityRecord 로 반환.

    signal_source 가 주어지면 해당 source 의 레코드만 반환한다. entry shadow 와 exit
    shadow(`event_shadow_exit`)가 같은 jsonl 에 섞여 있으므로, 분석 시 한쪽만 골라야
    entry/exit 가 교차 매칭되지 않는다. signal_source 필드가 없는 레거시 레코드는
    entry(`event_shadow`)로 간주한다 (P2 2-4 exit)."""
    shadow_dir = Path(shadow_dir)
    if not shadow_dir.exists():
        return []

    out: List[ParityRecord] = []
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
                strategy = raw.get("strategy") or ""
                if strategy_filter and strategy != strategy_filter:
                    continue
                rec_source = raw.get("signal_source") or "event_shadow"
                if signal_source is not None and rec_source != signal_source:
                    continue
                code = raw.get("code") or ""
                signal = raw.get("signal") or {}
                action = signal.get("action") or "BUY"
                recorded_at = float(raw.get("recorded_at") or 0.0)
                if not code or recorded_at <= 0:
                    continue
                trade_date, ts_iso = _epoch_to_kst(recorded_at)
                # 파일명과 trade_date 가 어긋나는 라인(타임존 경계 케이스)은 파일명 우선
                if not (date_from <= trade_date <= date_to):
                    continue
                out.append(ParityRecord(
                    source="event_shadow",
                    strategy=strategy,
                    code=code,
                    action=action,
                    ts_epoch=recorded_at,
                    ts_iso=ts_iso,
                    trade_date=trade_date,
                    raw=raw,
                ))
    return out


def load_polling_sells(
    db_path: Path,
    date_from: str,
    date_to: str,
    strategy_filter: Optional[str] = None,
) -> Dict[Tuple[str, str, str], List[float]]:
    """SELL signal_history 의 `return_rate` 를 (strategy, code, trade_date) 키로 묶어 반환.

    polling_only(=fast path miss) 케이스의 "missed PnL" 산출에 사용한다. 같은
    BUY 와 짝지을 SELL 의 return_rate 가 fast path 단독 운영 시 잃었을 PnL 의
    근사치다.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return {}

    from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    to_iso = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"

    sql = (
        "SELECT strategy_name, code, return_rate, timestamp "
        "FROM signal_history "
        "WHERE substr(timestamp, 1, 10) BETWEEN ? AND ? "
        "  AND action = 'SELL' "
        "  AND return_rate IS NOT NULL"
    )
    params: list = [from_iso, to_iso]
    if strategy_filter:
        sql += " AND strategy_name = ?"
        params.append(strategy_filter)
    sql += " ORDER BY timestamp ASC"

    out: Dict[Tuple[str, str, str], List[float]] = {}
    with sqlite3.connect(db_path) as conn:
        for strategy_name, code, return_rate, timestamp in conn.execute(sql, params):
            try:
                ts_epoch = _kst_str_to_epoch(timestamp)
            except ValueError:
                continue
            trade_date, _ = _epoch_to_kst(ts_epoch)
            out.setdefault((strategy_name, code, trade_date), []).append(float(return_rate))
    return out


def load_polling_records(
    db_path: Path,
    date_from: str,
    date_to: str,
    strategy_filter: Optional[str] = None,
    action: str = "BUY",
    require_api_success: bool = True,
) -> List[ParityRecord]:
    """`scheduler.db` 의 signal_history 에서 조건에 맞는 신호를 ParityRecord 로 반환."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    # date_from/to (YYYYMMDD) → "YYYY-MM-DD" (timestamp 의 date prefix 비교용)
    from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    to_iso = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"

    sql = (
        "SELECT strategy_name, code, name, action, price, qty, "
        "       return_rate, reason, timestamp, api_success "
        "FROM signal_history "
        "WHERE substr(timestamp, 1, 10) BETWEEN ? AND ? "
        "  AND action = ?"
    )
    params: list = [from_iso, to_iso, action]
    if strategy_filter:
        sql += " AND strategy_name = ?"
        params.append(strategy_filter)
    if require_api_success:
        sql += " AND api_success = 1"
    sql += " ORDER BY timestamp ASC"

    out: List[ParityRecord] = []
    with sqlite3.connect(db_path) as conn:
        for row in conn.execute(sql, params):
            (strategy_name, code, name, act, price, qty,
             return_rate, reason, timestamp, api_success) = row
            try:
                ts_epoch = _kst_str_to_epoch(timestamp)
            except ValueError:
                continue
            trade_date, ts_iso = _epoch_to_kst(ts_epoch)
            out.append(ParityRecord(
                source="polling",
                strategy=strategy_name,
                code=code,
                action=act,
                ts_epoch=ts_epoch,
                ts_iso=ts_iso,
                trade_date=trade_date,
                raw={
                    "name": name, "price": price, "qty": qty,
                    "return_rate": return_rate, "reason": reason,
                    "timestamp": timestamp, "api_success": bool(api_success),
                },
            ))
    return out


# ── Report ─────────────────────────────────────────────────────────────────

def _group_key(rec: ParityRecord) -> Tuple[str, str, str]:
    return (rec.strategy, rec.code, rec.trade_date)


def _record_to_brief(rec: ParityRecord) -> Dict[str, Any]:
    return {
        "strategy": rec.strategy,
        "code": rec.code,
        "trade_date": rec.trade_date,
        "ts_iso": rec.ts_iso,
        "ts_epoch": rec.ts_epoch,
    }


def _extract_shadow_price(rec: ParityRecord) -> Optional[float]:
    sig = rec.raw.get("signal") or {}
    p = sig.get("price")
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def _extract_polling_price(rec: ParityRecord) -> Optional[float]:
    p = rec.raw.get("price")
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def _price_divergence_stats(
    matched_pairs: List[Tuple[ParityRecord, ParityRecord]],
) -> Dict[str, Any]:
    diffs: List[float] = []
    diffs_pct: List[float] = []
    for s, p in matched_pairs:
        sp = _extract_shadow_price(s)
        pp = _extract_polling_price(p)
        if sp is None or pp is None or pp == 0:
            continue
        diffs.append(sp - pp)
        diffs_pct.append((sp - pp) / pp * 100.0)
    if not diffs:
        return {
            "count": 0,
            "avg_diff": None, "avg_diff_pct": None,
            "median_diff_pct": None,
            "min_diff_pct": None, "max_diff_pct": None,
        }
    return {
        "count": len(diffs),
        "avg_diff": sum(diffs) / len(diffs),
        "avg_diff_pct": sum(diffs_pct) / len(diffs_pct),
        "median_diff_pct": statistics.median(diffs_pct),
        "min_diff_pct": min(diffs_pct),
        "max_diff_pct": max(diffs_pct),
    }


def _missed_pnl_stats(
    polling_only: List[ParityRecord],
    polling_sells_by_key: Optional[Dict[Tuple[str, str, str], List[float]]],
) -> Dict[str, Any]:
    """polling_only(fast path miss) 의 missed PnL.

    matching SELL 의 return_rate 를 그 trade 의 실현 PnL 근사치로 사용한다.
    shadow_only 는 실제 체결이 없어 PnL 산출 불가 — 여기서 다루지 않는다.
    """
    sells = polling_sells_by_key or {}
    known: List[float] = []
    unknown = 0
    for r in polling_only:
        rates = sells.get((r.strategy, r.code, r.trade_date))
        if rates:
            known.append(rates[0])
        else:
            unknown += 1
    if not known:
        return {
            "known_count": 0, "unknown_count": unknown,
            "avg_return_rate": None, "sum_return_rate": None,
            "median_return_rate": None,
        }
    return {
        "known_count": len(known),
        "unknown_count": unknown,
        "avg_return_rate": sum(known) / len(known),
        "sum_return_rate": sum(known),
        "median_return_rate": statistics.median(known),
    }


def compute_parity_report(
    shadow: List[ParityRecord],
    polling: List[ParityRecord],
    match_window_sec: float,
    polling_sells_by_key: Optional[Dict[Tuple[str, str, str], List[float]]] = None,
) -> Dict[str, Any]:
    groups: Dict[Tuple[str, str, str], Dict[str, List[ParityRecord]]] = {}
    for r in shadow:
        groups.setdefault(_group_key(r), {"shadow": [], "polling": []})["shadow"].append(r)
    for r in polling:
        groups.setdefault(_group_key(r), {"shadow": [], "polling": []})["polling"].append(r)

    matched_pairs: List[Tuple[ParityRecord, ParityRecord]] = []
    shadow_only: List[ParityRecord] = []
    polling_only: List[ParityRecord] = []
    duplicates_shadow: List[ParityRecord] = []
    duplicates_polling: List[ParityRecord] = []

    for key, bucket in groups.items():
        s_sorted = sorted(bucket["shadow"], key=lambda r: r.ts_epoch)
        p_sorted = sorted(bucket["polling"], key=lambda r: r.ts_epoch)
        if len(s_sorted) > 1:
            duplicates_shadow.extend(s_sorted[1:])
        if len(p_sorted) > 1:
            duplicates_polling.extend(p_sorted[1:])
        s0 = s_sorted[0] if s_sorted else None
        p0 = p_sorted[0] if p_sorted else None
        if s0 and p0:
            if abs(s0.ts_epoch - p0.ts_epoch) <= match_window_sec:
                matched_pairs.append((s0, p0))
            else:
                shadow_only.append(s0)
                polling_only.append(p0)
        elif s0:
            shadow_only.append(s0)
        elif p0:
            polling_only.append(p0)

    lead_times = [s.ts_epoch - p.ts_epoch for s, p in matched_pairs]
    lead_stats = _lead_time_stats(lead_times)

    per_date: Dict[str, Dict[str, int]] = {}
    for s, p in matched_pairs:
        per_date.setdefault(s.trade_date, _empty_bucket())["matched"] += 1
    for r in shadow_only:
        per_date.setdefault(r.trade_date, _empty_bucket())["shadow_only"] += 1
    for r in polling_only:
        per_date.setdefault(r.trade_date, _empty_bucket())["polling_only"] += 1
    for r in duplicates_shadow:
        per_date.setdefault(r.trade_date, _empty_bucket())["duplicates_shadow"] += 1
    for r in duplicates_polling:
        per_date.setdefault(r.trade_date, _empty_bucket())["duplicates_polling"] += 1

    total_matched = len(matched_pairs)
    total_shadow_only = len(shadow_only)
    total_polling_only = len(polling_only)
    denom = total_matched + total_shadow_only + total_polling_only
    match_rate = (total_matched / denom) if denom else 0.0

    price_div = _price_divergence_stats(matched_pairs)
    missed_pnl = _missed_pnl_stats(polling_only, polling_sells_by_key)

    return {
        "totals": {
            "matched": total_matched,
            "shadow_only": total_shadow_only,
            "polling_only": total_polling_only,
            "duplicates_shadow": len(duplicates_shadow),
            "duplicates_polling": len(duplicates_polling),
            "match_rate": match_rate,
        },
        "lead_time_seconds": lead_stats,
        "price_divergence": price_div,
        "missed_pnl": missed_pnl,
        "per_date": dict(sorted(per_date.items())),
        "details": {
            "matched": [
                {"shadow": _record_to_brief(s), "polling": _record_to_brief(p),
                 "lead_time_seconds": s.ts_epoch - p.ts_epoch}
                for s, p in matched_pairs
            ],
            "shadow_only": [_record_to_brief(r) for r in shadow_only],
            "polling_only": [_record_to_brief(r) for r in polling_only],
            "duplicates_shadow": [_record_to_brief(r) for r in duplicates_shadow],
            "duplicates_polling": [_record_to_brief(r) for r in duplicates_polling],
        },
    }


def _empty_bucket() -> Dict[str, int]:
    return {"matched": 0, "shadow_only": 0, "polling_only": 0,
            "duplicates_shadow": 0, "duplicates_polling": 0}


def _lead_time_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": sum(values) / len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


# ── Markdown rendering ─────────────────────────────────────────────────────

def format_markdown_report(report: Dict[str, Any]) -> str:
    cfg = report.get("config", {})
    totals = report["totals"]
    lt = report["lead_time_seconds"]
    per_date = report.get("per_date", {})

    lines: List[str] = []
    lines.append("# VBO Shadow ↔ Polling Parity Report")
    lines.append("")
    if cfg:
        lines.append(
            f"- strategy: `{cfg.get('strategy', '')}`  "
            f"date: `{cfg.get('date_from', '')} ~ {cfg.get('date_to', '')}`  "
            f"window: `{cfg.get('match_window_sec', '')}s`"
        )
        lines.append("")

    lines.append("## 전체 집계")
    lines.append("")
    lines.append("| 항목 | 건수 |")
    lines.append("|---|---:|")
    lines.append(f"| matched (full gate parity) | {totals['matched']} |")
    lines.append(f"| shadow_only (fast path over-fire) | {totals['shadow_only']} |")
    lines.append(f"| polling_only (fast path miss) | {totals['polling_only']} |")
    lines.append(f"| duplicates_shadow | {totals['duplicates_shadow']} |")
    lines.append(f"| duplicates_polling | {totals['duplicates_polling']} |")
    lines.append(f"| match_rate | {totals['match_rate']:.3f} |")
    lines.append("")

    lines.append("## Lead time (shadow_ts − polling_ts, seconds)")
    lines.append("")
    if lt.get("count"):
        lines.append(f"- count: {lt['count']}")
        lines.append(f"- avg: {lt['avg']:.3f}")
        lines.append(f"- median: {lt['median']:.3f}")
        lines.append(f"- min: {lt['min']:.3f}")
        lines.append(f"- max: {lt['max']:.3f}")
    else:
        lines.append("- (no matched pairs)")
    lines.append("")

    div = report.get("price_divergence") or {}
    lines.append("## Price divergence (shadow_price − polling_price, matched pairs)")
    lines.append("")
    if div.get("count"):
        lines.append(f"- count: {div['count']}")
        lines.append(f"- avg_diff: {div['avg_diff']:.3f}")
        lines.append(f"- avg_diff_pct: {div['avg_diff_pct']:.3f}%")
        lines.append(f"- median_diff_pct: {div['median_diff_pct']:.3f}%")
        lines.append(f"- min_diff_pct: {div['min_diff_pct']:.3f}%")
        lines.append(f"- max_diff_pct: {div['max_diff_pct']:.3f}%")
    else:
        lines.append("- (no matched pairs with price data)")
    lines.append("")

    mp = report.get("missed_pnl") or {}
    lines.append("## Missed trade PnL (polling_only, fast path miss)")
    lines.append("")
    if mp.get("known_count"):
        lines.append(f"- known_count: {mp['known_count']}")
        lines.append(f"- unknown_count: {mp['unknown_count']}")
        lines.append(f"- avg_return_rate: {mp['avg_return_rate']:.3f}%")
        lines.append(f"- median_return_rate: {mp['median_return_rate']:.3f}%")
        lines.append(f"- sum_return_rate: {mp['sum_return_rate']:.3f}%")
    else:
        lines.append(f"- known_count: 0")
        lines.append(f"- unknown_count: {mp.get('unknown_count', 0)}")
        lines.append("- (no matching SELL records — PnL 미산정)")
    lines.append("")

    if per_date:
        lines.append("## 거래일별")
        lines.append("")
        lines.append("| date | matched | shadow_only | polling_only | dup_s | dup_p |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for date, bucket in per_date.items():
            lines.append(
                f"| {date} | {bucket['matched']} | {bucket['shadow_only']} | "
                f"{bucket['polling_only']} | {bucket['duplicates_shadow']} | "
                f"{bucket['duplicates_polling']} |"
            )
        lines.append("")

    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VBO shadow jsonl ↔ polling signal_history parity 분석",
    )
    parser.add_argument("--shadow-dir", default="logs/strategies/event_shadow")
    parser.add_argument("--scheduler-db", default="data/StrategyScheduler/scheduler.db")
    parser.add_argument("--date-from", required=True, help="YYYYMMDD")
    parser.add_argument("--date-to", required=True, help="YYYYMMDD")
    parser.add_argument("--strategy", default=_DEFAULT_STRATEGY)
    # entry shadow(event_shadow / BUY) vs exit shadow(event_shadow_exit / SELL) 분리 분석.
    parser.add_argument("--signal-source", default="event_shadow",
                        choices=["event_shadow", "event_shadow_exit"])
    parser.add_argument("--polling-action", default="BUY", choices=["BUY", "SELL"])
    parser.add_argument("--match-window-sec", type=float, default=300.0)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    shadow = load_shadow_records(
        shadow_dir=Path(args.shadow_dir),
        date_from=args.date_from,
        date_to=args.date_to,
        strategy_filter=args.strategy or None,
        signal_source=args.signal_source,
    )
    polling = load_polling_records(
        db_path=Path(args.scheduler_db),
        date_from=args.date_from,
        date_to=args.date_to,
        strategy_filter=args.strategy or None,
        action=args.polling_action,
        require_api_success=True,
    )
    polling_sells = load_polling_sells(
        db_path=Path(args.scheduler_db),
        date_from=args.date_from,
        date_to=args.date_to,
        strategy_filter=args.strategy or None,
    )

    report = compute_parity_report(
        shadow=shadow,
        polling=polling,
        match_window_sec=args.match_window_sec,
        polling_sells_by_key=polling_sells,
    )
    report["config"] = {
        "strategy": args.strategy,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "match_window_sec": args.match_window_sec,
        "signal_source": args.signal_source,
        "polling_action": args.polling_action,
        "shadow_dir": str(args.shadow_dir),
        "scheduler_db": str(args.scheduler_db),
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
