"""장중 paper vs 일봉 근사 교차검증 — 일봉 백테스트가 실행 대비 얼마나 낙관적인가.

같은 거래일·종목에 대해 두 기록을 대조한다:
  - `overseas_paper`  : 장중 REST 폴링으로 실제 판정된 진입/청산(would-be 주문)
  - `overseas_dryrun` : 마감 후 완성 일봉으로 사후 산출한 신호(비관·낙관 bracket)

핵심 질문은 하나다. **일봉 근사는 목표가에 체결됐다고 가정하지만, 장중 경로는 목표가를
넘은 시점의 폴링가에 진입한다.** 그 차이(진입 슬리피지)와 실현수익 격차를 재면, 일봉
기반 스윕 결과(`scripts/run_overseas_vbo_sweep.py`)를 얼마나 할인해서 읽어야 하는지 알 수 있다.

신호 커버리지도 양방향으로 센다:
  - daily_only    : 일봉은 신호를 냈는데 장중엔 진입 없음(폴링 간격·최대보유·준비지연 등)
  - intraday_only : 장중엔 진입했는데 일봉 근사로는 신호 없음
  - unclosed      : 진입 후 청산 기록이 없는 미완결(재시작 등)

사용:
    conda activate py310
    python scripts/compare_overseas_intraday_vs_daily.py --date-from 20260805 --date-to 20260819
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DAILY_SOURCE = "overseas_dryrun"
INTRADAY_SOURCE = "overseas_paper"
DEFAULT_ROUND_TRIP_COST_PCT = 0.5

Key = Tuple[str, str]  # (trade_date, code)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def load_records(
    shadow_dir: Path | str,
    date_from: str,
    date_to: str,
) -> Tuple[Dict[Key, Dict[str, Any]], Dict[Key, Dict[str, Any]]]:
    """shadow 저널에서 (일봉 신호, 장중 왕복) 을 (거래일, 종목) 키로 모아 반환한다.

    장중은 BUY/SELL 두 레코드를 한 왕복으로 합친다. SELL 이 없으면 realized_pct=None
    (미완결)로 남긴다.
    """
    shadow_dir = Path(shadow_dir)
    daily: Dict[Key, Dict[str, Any]] = {}
    intraday: Dict[Key, Dict[str, Any]] = {}
    if not shadow_dir.exists():
        return daily, intraday

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
                source = raw.get("signal_source") or ""
                signal = raw.get("signal") or {}
                code = raw.get("code") or signal.get("code") or ""
                if not code:
                    continue

                if source == DAILY_SOURCE:
                    key = (str(signal.get("date") or stem), code)
                    daily[key] = {
                        "target": _to_float(signal.get("target") or signal.get("entry_price")),
                        "pessimistic": _to_float(signal.get("realized_pct_pessimistic")),
                        "optimistic": _to_float(signal.get("realized_pct_optimistic")),
                        "decidable": bool(signal.get("exit_decidable")),
                    }
                elif source == INTRADAY_SOURCE:
                    inner = signal.get("signal") or {}
                    key = (str(inner.get("date") or stem), code)
                    entry = intraday.setdefault(key, {
                        "entry_price": None, "exit_price": None,
                        "realized_pct": None, "exit_reason": None,
                    })
                    price = _to_float(signal.get("limit_price"))
                    if str(signal.get("side") or "").lower() == "buy":
                        entry["entry_price"] = price
                    else:
                        entry["exit_price"] = price
                        entry["realized_pct"] = _to_float(inner.get("realized_pct"))
                        entry["exit_reason"] = signal.get("exit_reason") or inner.get("exit_reason")
    return daily, intraday


def compare(
    daily: Dict[Key, Dict[str, Any]],
    intraday: Dict[Key, Dict[str, Any]],
    *,
    cost_pct: float = DEFAULT_ROUND_TRIP_COST_PCT,
) -> Dict[str, Any]:
    """두 기록을 대조해 슬리피지·실현수익 격차·커버리지를 집계한다."""
    matched_keys = set(daily) & set(intraday)
    slippages: List[float] = []
    intraday_nets: List[float] = []
    daily_optimistic: List[float] = []
    daily_pessimistic: List[float] = []
    by_exit_reason: Dict[str, int] = {}
    unclosed = 0

    for key in sorted(matched_keys):
        d, i = daily[key], intraday[key]
        if i.get("realized_pct") is None:
            unclosed += 1
            continue
        if d.get("target") and i.get("entry_price"):
            slippages.append((i["entry_price"] / d["target"] - 1) * 100.0)
        intraday_nets.append(i["realized_pct"] - cost_pct)
        if d.get("optimistic") is not None:
            daily_optimistic.append(d["optimistic"])
        if d.get("pessimistic") is not None:
            daily_pessimistic.append(d["pessimistic"])
        reason = i.get("exit_reason") or "unknown"
        by_exit_reason[reason] = by_exit_reason.get(reason, 0) + 1

    # 미완결(청산 없음)은 matched 로 세지 않되 별도 카운트한다.
    unclosed += sum(
        1 for k, v in intraday.items()
        if k not in matched_keys and v.get("realized_pct") is None
    )

    avg_opt = _avg(daily_optimistic)
    avg_intraday = _avg(intraday_nets)
    return {
        "matched": len(intraday_nets),
        "daily_only": len(set(daily) - set(intraday)),
        "intraday_only": len(set(intraday) - set(daily)),
        "unclosed": unclosed,
        "avg_entry_slippage_pct": _avg(slippages),
        "avg_intraday_net_pct": avg_intraday,
        "avg_daily_optimistic_pct": avg_opt,
        "avg_daily_pessimistic_pct": _avg(daily_pessimistic),
        "optimistic_minus_intraday_pct": (
            (avg_opt - avg_intraday) if (avg_opt is not None and avg_intraday is not None) else None
        ),
        "by_exit_reason": by_exit_reason,
    }


def format_markdown_report(report: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config or {}

    def _f(v, suffix=""):
        return f"{v:.3f}{suffix}" if isinstance(v, (int, float)) else "—"

    lines = ["# 장중 paper vs 일봉 근사 교차검증", ""]
    if cfg:
        lines.append(
            f"- 왕복 비용 {_f(cfg.get('cost_pct'))}%  "
            f"기간 `{cfg.get('date_from', '')} ~ {cfg.get('date_to', '')}`"
        )
        lines.append("")
    lines.append(
        "일봉 근사는 목표가 체결을 가정하지만 장중 경로는 목표가를 넘은 시점의 폴링가에 "
        "진입합니다. **진입 슬리피지**가 양수면 그만큼 일봉 스윕 결과를 할인해 읽어야 합니다."
    )
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---:|")
    lines.append(f"| matched (양쪽 모두 왕복 성립) | {report['matched']} |")
    lines.append(f"| daily_only (일봉만 신호) | {report['daily_only']} |")
    lines.append(f"| intraday_only (장중만 진입) | {report['intraday_only']} |")
    lines.append(f"| unclosed (청산 기록 없음) | {report['unclosed']} |")
    lines.append(f"| **진입 슬리피지 평균** | {_f(report['avg_entry_slippage_pct'], '%')} |")
    lines.append(f"| 장중 실현(비용후) 평균 | {_f(report['avg_intraday_net_pct'], '%')} |")
    lines.append(f"| 일봉 낙관 평균 | {_f(report['avg_daily_optimistic_pct'], '%')} |")
    lines.append(f"| 일봉 비관 평균 | {_f(report['avg_daily_pessimistic_pct'], '%')} |")
    lines.append(f"| **optimistic_minus_intraday** | {_f(report['optimistic_minus_intraday_pct'], '%p')} |")
    lines.append("")
    if report.get("by_exit_reason"):
        lines.append("| 장중 청산 사유 | 건수 |")
        lines.append("|---|---:|")
        for reason, n in sorted(report["by_exit_reason"].items()):
            lines.append(f"| {reason} | {n} |")
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="장중 paper vs 일봉 근사 교차검증")
    parser.add_argument("--shadow-dir", default="logs/strategies/event_shadow")
    parser.add_argument("--date-from", required=True, help="YYYYMMDD")
    parser.add_argument("--date-to", required=True, help="YYYYMMDD")
    parser.add_argument("--cost-pct", type=float, default=DEFAULT_ROUND_TRIP_COST_PCT,
                        help="왕복 비용 %%. 장중 실현수익에 적용한다(일봉 bracket 은 이미 gross).")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    daily, intraday = load_records(args.shadow_dir, args.date_from, args.date_to)
    report = compare(daily, intraday, cost_pct=args.cost_pct)
    config = {
        "shadow_dir": str(args.shadow_dir),
        "date_from": args.date_from,
        "date_to": args.date_to,
        "cost_pct": args.cost_pct,
    }

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"config": config, "report": report}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"[INFO] JSON report: {out}")
    if args.output_markdown:
        out = Path(args.output_markdown)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(format_markdown_report(report, config), encoding="utf-8")
        print(f"[INFO] Markdown report: {out}")
    if not (args.output_json or args.output_markdown):
        print(format_markdown_report(report, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
