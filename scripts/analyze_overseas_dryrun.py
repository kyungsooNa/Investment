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
                    "stop_price": _to_float(signal.get("stop_price")),
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


def _f0(value: Any) -> float:
    """float 변환 실패 시 0.0(봉 OHLC 파싱용)."""
    try:
        return float(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0.0


def reconstruct_multiday_exit(
    entry_price: float,
    stop_price: float,
    bars: List[Dict[str, Any]],
    *,
    trailing_stop_pct: Optional[float],
    time_stop_days: Optional[int],
    round_trip_cost_pct: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """확정 진입(entry_price/stop_price)에 멀티데이 청산 규칙을 회고 적용한다.

    `bars`는 진입일(포함) 이후 일봉 오름차순. 규칙은 `MultiDayDailyBreakoutBacktest`와
    동일하다: (1) hard stop — 저가 ≤ stop, gap-through 시 시가 체결(보수적),
    (2) trailing — 고점×(1−trail/100) 하향 이탈(이익 구간), (3) time — 보유일수 ≥
    time_stop_days 종가 청산, (4) terminal — 미청산 시 마지막 봉 종가.

    same-day 청산만 담던 dry-run 신호를 "만약 멀티세션 보유했다면"으로 회고 재구성해
    same-day 모델이 과소/과대 평가하는 정도(GAP)를 측정하기 위함이다. 데이터 끝까지의
    재구성이므로 미래 봉이 필요 — 신호 시점엔 알 수 없는 회고값임에 유의.
    """
    rows = [b for b in (bars or []) if isinstance(b, dict)]
    if entry_price <= 0 or not rows:
        return None

    peak = entry_price
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_idx = len(rows) - 1
    for j, bar in enumerate(rows):
        o = _f0(bar.get("open"))
        h = _f0(bar.get("high"))
        low = _f0(bar.get("low"))
        c = _f0(bar.get("close"))

        if stop_price > 0 and low <= stop_price:
            exit_price = o if (0 < o < stop_price) else stop_price
            exit_reason, exit_idx = "stop", j
            break

        if h > peak:
            peak = h
        if trailing_stop_pct:
            trail = peak * (1 - float(trailing_stop_pct) / 100.0)
            if trail > stop_price and low <= trail:
                exit_price = o if (0 < o < trail) else trail
                exit_reason, exit_idx = "trailing", j
                break

        if time_stop_days and j >= int(time_stop_days):
            exit_price, exit_reason, exit_idx = c, "time", j
            break

    if exit_price is None:
        exit_price = _f0(rows[-1].get("close"))
        exit_reason, exit_idx = "terminal", len(rows) - 1

    gross = (exit_price / entry_price - 1) * 100.0
    return {
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "holding_days": exit_idx,  # 진입일=0
        "gross_return_pct": gross,
        "net_return_pct": gross - float(round_trip_cost_pct),
    }


def load_ohlcv_dir(ohlcv_dir: Path | str) -> Dict[str, List[Dict[str, Any]]]:
    """`<ohlcv_dir>/<CODE>.jsonl`(한 줄=일봉 dict)을 종목코드→오름차순 봉 list 로 로드."""
    ohlcv_dir = Path(ohlcv_dir)
    if not ohlcv_dir.exists():
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(ohlcv_dir.glob("*.jsonl")):
        code = path.stem
        bars: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(bar, dict):
                    bars.append(bar)
        bars.sort(key=lambda b: str(b.get("date") or ""))
        out[code] = bars
    return out


def compute_multiday_report(
    records: List[Dict[str, Any]],
    ohlcv_by_code: Dict[str, List[Dict[str, Any]]],
    *,
    trailing_stop_pct: Optional[float],
    time_stop_days: Optional[int],
    cost_pct: float,
) -> Dict[str, Any]:
    """누적 dry-run 신호 + per-code OHLCV 로 멀티세션 보유 성과를 회고 재구성한다.

    각 신호의 진입일(`trade_date`) 이후 봉으로 청산을 시뮬해 same-day 모델 대비
    GAP(멀티데이 평균 gross − same-day 평균 realized)을 집계한다. OHLCV 가 없는
    신호는 unmatched 로 분리한다.
    """
    nets: List[float] = []
    grosses: List[float] = []
    holding_days: List[int] = []
    same_day_matched: List[float] = []
    by_exit_reason: Dict[str, int] = {}
    unmatched = 0

    for r in records:
        entry = r.get("entry_price")
        stop = r.get("stop_price")
        code = r.get("code") or ""
        trade_date = str(r.get("trade_date") or "")
        all_bars = ohlcv_by_code.get(code)
        if entry is None or stop is None or not all_bars:
            unmatched += 1
            continue
        future = [b for b in all_bars if str(b.get("date") or "") >= trade_date]
        result = reconstruct_multiday_exit(
            float(entry), float(stop), future,
            trailing_stop_pct=trailing_stop_pct,
            time_stop_days=time_stop_days,
            round_trip_cost_pct=cost_pct,
        )
        if result is None:
            unmatched += 1
            continue
        nets.append(result["net_return_pct"])
        grosses.append(result["gross_return_pct"])
        holding_days.append(result["holding_days"])
        reason = result["exit_reason"] or "unknown"
        by_exit_reason[reason] = by_exit_reason.get(reason, 0) + 1
        sd = r.get("realized_pct")
        if sd is not None:
            same_day_matched.append(float(sd))

    nstats = _stats(nets)
    gstats = _stats(grosses)
    same_day_avg = (sum(same_day_matched) / len(same_day_matched)) if same_day_matched else None
    multiday_avg = gstats["avg"]
    gap = (multiday_avg - same_day_avg) if (multiday_avg is not None and same_day_avg is not None) else None
    wins = sum(1 for x in nets if x > 0)
    losses = sum(1 for x in nets if x < 0)

    return {
        "reconstructed_count": len(nets),
        "unmatched_count": unmatched,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(nets)) if nets else None,
        "avg_holding_days": (sum(holding_days) / len(holding_days)) if holding_days else None,
        "net_return_pct": nstats,
        "gross_return_pct": gstats,
        "by_exit_reason": by_exit_reason,
        "same_day_vs_multiday": {
            "same_day_avg_realized_pct": same_day_avg,
            "multiday_avg_gross_pct": multiday_avg,
            "gap_pct": gap,
        },
        "params": {
            "trailing_stop_pct": trailing_stop_pct,
            "time_stop_days": time_stop_days,
            "round_trip_cost_pct": cost_pct,
        },
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

    md = report.get("multiday")
    if md:
        g = md.get("same_day_vs_multiday", {})
        ng = md.get("net_return_pct", {})
        lines.append("## 멀티데이 회고 재구성 (would-be 멀티세션 보유)")
        lines.append("")
        lines.append(f"- reconstructed_count: {md.get('reconstructed_count', 0)}")
        lines.append(f"- unmatched_count: {md.get('unmatched_count', 0)}")
        lines.append(f"- win_rate: {_fmt(md.get('win_rate'))}")
        lines.append(f"- avg_holding_days: {_fmt(md.get('avg_holding_days'))}")
        lines.append(f"- avg_net_return_pct: {_fmt(ng.get('avg'), '%')}")
        lines.append(f"- same_day_avg_realized_pct: {_fmt(g.get('same_day_avg_realized_pct'), '%')}")
        lines.append(f"- multiday_avg_gross_pct: {_fmt(g.get('multiday_avg_gross_pct'), '%')}")
        lines.append(f"- **gap_pct (multiday − same_day): {_fmt(g.get('gap_pct'), '%')}**")
        lines.append("")
        lines.append("| exit_reason | count |")
        lines.append("| --- | ---: |")
        for reason, cnt in sorted(md.get("by_exit_reason", {}).items()):
            lines.append(f"| {reason} | {cnt} |")
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
    parser.add_argument("--ohlcv-dir", default=None,
                        help="멀티데이 회고 재구성용 per-code 일봉 디렉토리(<CODE>.jsonl). 지정 시 multiday 섹션 추가.")
    parser.add_argument("--trailing-stop-pct", type=float, default=8.0,
                        help="멀티데이 trailing stop %% (회고 가정값)")
    parser.add_argument("--time-stop-days", type=int, default=20,
                        help="멀티데이 time stop 보유일수(회고 가정값)")
    parser.add_argument("--cost-pct", type=float, default=0.2,
                        help="멀티데이 왕복 비용 %% (net 산출)")
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

    if args.ohlcv_dir:
        ohlcv_by_code = load_ohlcv_dir(args.ohlcv_dir)
        report["multiday"] = compute_multiday_report(
            records, ohlcv_by_code,
            trailing_stop_pct=args.trailing_stop_pct,
            time_stop_days=args.time_stop_days,
            cost_pct=args.cost_pct,
        )
        report["config"]["ohlcv_dir"] = str(args.ohlcv_dir)

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
