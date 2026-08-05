"""해외 VBO 파라미터 스윕 — K · 손절 · 목표가 괴리상한 그리드 비교 (오프라인).

편향 수정(#769) 이후의 `OverseasDailyVBOBacktest` 로 파라미터 조합을 돌려, 조합별
거래수·판정 가능 비율·비관/낙관 평균수익을 표로 낸다. 어느 조합에 엣지가 있는지
**보기 전에 라이브 파라미터를 만지지 않기 위한** 사전 측정 도구다.

판정 불가(undecided): 일봉은 저가 발생 시각을 담지 않아 `저 <= 손절가` 건의 손절
체결 여부를 확정할 수 없다. 비관(손절 가정)·낙관(종가 가정) 양끝이 참값을 감싸며,
어느 쪽도 단독으로는 기댓값이 아니다. 판정 불가 비율이 높은 조합은 그만큼 결론이
약하므로 함께 본다.

입력은 per-code 일봉 JSONL 디렉토리(`<CODE>.jsonl`, 한 줄 = 일봉 dict)로,
`scripts/analyze_overseas_dryrun.py --ohlcv-dir` 와 같은 포맷이다.

사용:
    conda activate py310
    python scripts/run_overseas_vbo_sweep.py --ohlcv-dir data/overseas_ohlcv
    python scripts/run_overseas_vbo_sweep.py --ohlcv-dir data/overseas_ohlcv \\
        --k-values 0.3,0.5,0.7 --stop-values=-2,-3,-5 --gap-caps none,1.5,2.5 \\
        --date-from 20260601 --date-to 20260731 --output-markdown reports/vbo_sweep.md

주의: 손절값은 음수라 `--stop-values=-2,-3,-5` 처럼 `=` 로 붙여야 한다
(공백으로 두면 argparse 가 옵션으로 해석한다).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_overseas_dryrun import load_ohlcv_dir  # noqa: E402
from strategies.overseas_daily_vbo_backtest import OverseasDailyVBOBacktest  # noqa: E402

DEFAULT_ROUND_TRIP_COST_PCT = 0.5
_NONE_TOKENS = {"none", "null", "off", "-"}


def parse_grid(spec: str) -> List[Optional[float]]:
    """"0.3,0.5" → [0.3, 0.5]. "none" 토큰은 '필터 없음'(None) 으로 매핑한다."""
    values: List[Optional[float]] = []
    for token in str(spec or "").split(","):
        token = token.strip()
        if not token:
            continue
        if token.lower() in _NONE_TOKENS:
            values.append(None)
            continue
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def _slice_bars(bars: List[Dict[str, Any]], date_from: Optional[str], date_to: Optional[str]):
    if not (date_from or date_to):
        return bars
    lo = date_from or ""
    hi = date_to or "99999999"
    return [b for b in bars if lo <= str(b.get("date") or "") <= hi]


def _avg(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def ohlcv_coverage(
    ohlcv_dir: Path | str,
    date_from: Optional[str],
    date_to: Optional[str],
) -> Dict[str, Any]:
    """스윕이 실제로 사용한 봉 구간·심볼 수. 기간 인자 미지정 시 리포트 표기용."""
    dates: List[str] = []
    symbols = 0
    for bars in load_ohlcv_dir(ohlcv_dir).values():
        sliced = _slice_bars(bars, date_from, date_to)
        if not sliced:
            continue
        symbols += 1
        dates.extend(str(b.get("date") or "") for b in sliced if b.get("date"))
    return {
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "symbols": symbols,
    }


def run_sweep(
    *,
    ohlcv_dir: Path | str,
    k_values: List[float],
    stop_values: List[float],
    gap_caps: List[Optional[float]],
    cost_pct: float = DEFAULT_ROUND_TRIP_COST_PCT,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """파라미터 조합별로 전 심볼 백테스트를 돌려 집계 행 목록을 반환한다."""
    ohlcv_by_code = load_ohlcv_dir(ohlcv_dir)
    sliced = {
        code: _slice_bars(bars, date_from, date_to)
        for code, bars in ohlcv_by_code.items()
    }

    rows: List[Dict[str, Any]] = []
    for k in k_values:
        for stop in stop_values:
            for cap in gap_caps:
                bt = OverseasDailyVBOBacktest(
                    k_value=k, stop_loss_pct=stop, round_trip_cost_pct=cost_pct,
                    max_gap_to_target_pct=cap,
                )
                nets: List[float] = []
                nets_opt: List[float] = []
                decided_nets: List[float] = []
                symbols_with_trades = 0
                for bars in sliced.values():
                    trades = bt.run_symbol(bars)["trades"]
                    if trades:
                        symbols_with_trades += 1
                    for t in trades:
                        nets.append(t["net_return_pct"])
                        nets_opt.append(t["net_return_pct_optimistic"])
                        if t["exit_decidable"]:
                            decided_nets.append(t["net_return_pct"])
                n = len(nets)
                rows.append({
                    "k": k,
                    "stop_loss_pct": stop,
                    "max_gap_to_target_pct": cap,
                    "trades": n,
                    "symbols": symbols_with_trades,
                    "decided": len(decided_nets),
                    "undecided": n - len(decided_nets),
                    "undecided_ratio": ((n - len(decided_nets)) / n) if n else None,
                    "avg_net_pessimistic": _avg(nets),
                    "avg_net_optimistic": _avg(nets_opt),
                    "avg_net_decided": _avg(decided_nets),
                    "win_rate_decided": (
                        sum(1 for x in decided_nets if x > 0) / len(decided_nets)
                    ) if decided_nets else None,
                })
    return rows


def format_markdown_report(rows: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config or {}

    def _f(v, suffix=""):
        return f"{v:.3f}{suffix}" if isinstance(v, (int, float)) else "—"

    lines: List[str] = ["# Overseas VBO Parameter Sweep", ""]
    if cfg:
        lines.append(
            f"- 왕복 비용 {_f(cfg.get('cost_pct'))}%  "
            f"데이터 `{cfg.get('first_date') or '?'} ~ {cfg.get('last_date') or '?'}` "
            f"({cfg.get('symbols', 0)}종목)  "
            f"ohlcv_dir `{cfg.get('ohlcv_dir', '')}`"
        )
        lines.append("")
    lines.append(
        "일봉은 저가 발생 시각을 담지 않아 `저 <= 손절가` 건은 **판정 불가**입니다. "
        "비관(손절 체결 가정)과 낙관(종가 청산 가정)이 참값을 감싸며, 어느 쪽도 단독으로는 "
        "기댓값이 아닙니다. 판정 불가 비율이 높은 조합일수록 결론이 약합니다."
    )
    lines.append("")
    lines.append("| K | stop% | gap상한% | trades | 판정 불가 | avg_net_pessimistic | avg_net_optimistic | avg_net_decided | win_decided |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        cap = "—" if r["max_gap_to_target_pct"] is None else f"{r['max_gap_to_target_pct']:.2f}"
        lines.append(
            f"| {r['k']} | {r['stop_loss_pct']} | {cap} | {r['trades']} | "
            f"{_f(r['undecided_ratio'])} | {_f(r['avg_net_pessimistic'], '%')} | "
            f"{_f(r['avg_net_optimistic'], '%')} | {_f(r['avg_net_decided'], '%')} | "
            f"{_f(r['win_rate_decided'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="해외 VBO 파라미터 스윕(오프라인)")
    parser.add_argument("--ohlcv-dir", required=True,
                        help="per-code 일봉 JSONL 디렉토리(<CODE>.jsonl)")
    parser.add_argument("--k-values", default="0.3,0.5,0.7")
    parser.add_argument("--stop-values", default="-2,-3,-5")
    parser.add_argument("--gap-caps", default="none,1.5,2.5",
                        help="목표가 괴리 상한(%%) 그리드. none 은 필터 없음 대조군.")
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--cost-pct", type=float, default=DEFAULT_ROUND_TRIP_COST_PCT,
                        help="왕복 비용 %%. 기본 0.5%% (미국주식 온라인 수수료 0.25%%/side 왕복).")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Windows 기본 콘솔(cp949)에서 표의 em-dash 가 UnicodeEncodeError 를 낸다.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    k_values = [v for v in parse_grid(args.k_values) if v is not None]
    stop_values = [v for v in parse_grid(args.stop_values) if v is not None]
    gap_caps = parse_grid(args.gap_caps) or [None]
    if not k_values or not stop_values:
        print("[ERROR] --k-values / --stop-values 에 유효한 숫자가 없습니다.")
        return 1

    rows = run_sweep(
        ohlcv_dir=args.ohlcv_dir,
        k_values=k_values,
        stop_values=stop_values,
        gap_caps=gap_caps,
        cost_pct=args.cost_pct,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    config = {
        "ohlcv_dir": str(args.ohlcv_dir),
        "date_from": args.date_from,
        "date_to": args.date_to,
        "cost_pct": args.cost_pct,
        **ohlcv_coverage(args.ohlcv_dir, args.date_from, args.date_to),
    }

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"config": config, "rows": rows}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"[INFO] JSON report: {out}")
    if args.output_markdown:
        out = Path(args.output_markdown)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(format_markdown_report(rows, config), encoding="utf-8")
        print(f"[INFO] Markdown report: {out}")
    if not (args.output_json or args.output_markdown):
        print(format_markdown_report(rows, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
