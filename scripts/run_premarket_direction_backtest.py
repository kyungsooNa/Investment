"""장전 코스피/코스닥 방향 예측 지표 백테스트.

밤사이 미국장·환율·변동성 지표가 한국 지수의 당일 방향을 얼마나 맞히는지 측정한다.

타깃(라벨):
    up_close (B) 종가 > 전일 종가   ← 주 타깃 ("양봉 마감")
    up_body  (C) 종가 > 시가        (차트상 양봉 캔들)
    up_gap   (A) 시가 > 전일 종가   (갭 방향)

정렬 규칙(미래참조 방지): 한국 거래일 D 의 특징값은 미국 세션 날짜 T < D 인 종가만 쓴다.
미국 T 세션은 KST 로 T+1 새벽에 마감하므로 D 장 시작 전에 확정된다. 값은
"한국 직전 거래일 이후 새로 확정된 미국 세션들의 누적 수익률"이다(한국 휴장 자동 처리).

데이터: 국내 지수 = 네이버 fchart, 해외 = Yahoo Finance chart API (네트워크 필요).

실행:
    python -m scripts.run_premarket_direction_backtest
    python -m scripts.run_premarket_direction_backtest --years 8 --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
from typing import Dict, List, Optional, Sequence

from services.premarket_direction_service import (
    GAP,
    KR_PREV,
    US_FEATURES,
    FeatureSpec,
    _fit_logit,
    build_dataset,
    parse_naver_fchart,
    parse_yahoo_chart,
)

LABELS = (("up_close", "B 종가>전일종가"), ("up_body", "C 종가>시가"), ("up_gap", "A 시가>전일종가"))
KR_INDICES = (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))
MODEL_FEATURES = [f.name for f in US_FEATURES] + [KR_PREV.name]


# ── 평가 ─────────────────────────────────────────────────────────────

def _signal_rows(rows, spec: FeatureSpec, min_abs: float = 0.0):
    for r in rows:
        x = r.get(spec.name)
        if x is None or x == 0 or abs(x) < min_abs:
            continue
        yield (spec.sign * x > 0), r


def sign_rule_hit_rate(rows, spec: FeatureSpec, label: str) -> dict:
    hits = [pred == bool(r[label]) for pred, r in _signal_rows(rows, spec)]
    return {"n": len(hits), "hit_rate": sum(hits) / len(hits) if hits else float("nan")}


def bucket_hit_rates(rows, spec: FeatureSpec, label: str, thresholds=(0.0, 0.01, 0.02)) -> List[dict]:
    out = []
    for t in thresholds:
        hits = [pred == bool(r[label]) for pred, r in _signal_rows(rows, spec, t)]
        out.append({"min_abs": t, "n": len(hits), "hit_rate": sum(hits) / len(hits) if hits else float("nan")})
    return out


def walk_forward_logit(rows, features: List[str], label: str, min_train: int = 250,
                       refit_every: int = 20) -> List[Optional[float]]:
    """확장 윈도우 walk-forward: i 번째 예측은 rows[:i] 로만 학습한 모델로 낸다."""
    import numpy as np

    complete = [all(r.get(f) is not None for f in features) for r in rows]
    preds: List[Optional[float]] = [None] * len(rows)
    model = None
    for i in range(min_train, len(rows)):
        if model is None or (i - min_train) % refit_every == 0:
            train = [r for r, ok in zip(rows[:i], complete[:i]) if ok]
            if len(train) < 30:
                continue
            model = _fit_logit([[r[f] for f in features] for r in train], [r[label] for r in train])
        if not complete[i]:
            continue
        mu, sd, w, b = model
        z = (np.asarray([rows[i][f] for f in features]) - mu) / sd
        preds[i] = float(1.0 / (1.0 + math.exp(-(z @ w + b))))
    return preds


def evaluate_model(rows, features: List[str], label: str, min_train: int, refit_every: int) -> dict:
    preds = walk_forward_logit(rows, features, label, min_train, refit_every)
    scored = [(p, r) for p, r in zip(preds, rows) if p is not None]
    if not scored:
        return {"n": 0}
    acc = sum((p > 0.5) == bool(r[label]) for p, r in scored) / len(scored)
    base_up = sum(r[label] for _, r in scored) / len(scored)
    prev_dir = [(r[KR_PREV.name] > 0) == bool(r[label]) for _, r in scored if r.get(KR_PREV.name)]
    conf = [(p, r) for p, r in scored if p >= 0.6 or p <= 0.4]
    conf_acc = sum((p > 0.5) == bool(r[label]) for p, r in conf) / len(conf) if conf else float("nan")
    return {
        "n": len(scored),
        "start": scored[0][1]["date"],
        "accuracy": acc,
        "baseline_majority": max(base_up, 1 - base_up),
        "baseline_prev_direction": sum(prev_dir) / len(prev_dir) if prev_dir else float("nan"),
        "confident_coverage": len(conf) / len(scored),
        "confident_accuracy": conf_acc,
    }


def analyze(rows, min_train: int = 250, refit_every: int = 20) -> dict:
    singles = {}
    for spec in (*US_FEATURES, KR_PREV, GAP):
        singles[spec.name] = {
            "label": spec.label,
            "sign": spec.sign,
            **{lab: sign_rule_hit_rate(rows, spec, lab) for lab, _ in LABELS},
            "buckets_up_close": bucket_hit_rates(rows, spec, "up_close"),
            "buckets_up_body": bucket_hit_rates(rows, spec, "up_body"),
        }
    return {
        "n": len(rows),
        "start": rows[0]["date"] if rows else None,
        "end": rows[-1]["date"] if rows else None,
        "base_rates": {lab: sum(r[lab] for r in rows) / len(rows) for lab, _ in LABELS} if rows else {},
        "single": singles,
        "model": {lab: evaluate_model(rows, MODEL_FEATURES, lab, min_train, refit_every) for lab, _ in LABELS},
        "model_with_gap_for_body": evaluate_model(rows, MODEL_FEATURES + [GAP.name], "up_body",
                                                  min_train, refit_every),
    }


# ── 리포트 ───────────────────────────────────────────────────────────

def _pct(x) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f}%"


def format_report(results: Dict[str, dict]) -> str:
    out = []
    for code, name in KR_INDICES:
        res = results.get(code)
        if not res:
            continue
        out.append(f"## {name} ({res['start']} ~ {res['end']}, {res['n']}일)")
        out.append("기저율: " + ", ".join(f"{t} {_pct(res['base_rates'][lab])}" for lab, t in LABELS))
        out.append("")
        out.append("| 지표 | 부호 | n | B 적중 | B(|x|≥1%) | B(|x|≥2%) | C 적중 | C(|x|≥1%) | A 적중 |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for s in res["single"].values():
            bb, cb = s["buckets_up_close"], s["buckets_up_body"]
            out.append(
                f"| {s['label']} | {'+' if s['sign'] > 0 else '−'} | {s['up_close']['n']} "
                f"| {_pct(s['up_close']['hit_rate'])} | {_pct(bb[1]['hit_rate'])} (n={bb[1]['n']}) "
                f"| {_pct(bb[2]['hit_rate'])} (n={bb[2]['n']}) | {_pct(s['up_body']['hit_rate'])} "
                f"| {_pct(cb[1]['hit_rate'])} (n={cb[1]['n']}) | {_pct(s['up_gap']['hit_rate'])} |"
            )
        out.append("")
        out.append("walk-forward 로지스틱(전 지표 결합, 표본외):")
        out.append("| 타깃 | n | 정확도 | 다수결 기준선 | 전일방향 기준선 | 확신(p≥.6/≤.4) 비중 | 확신 정확도 |")
        out.append("|---|---|---|---|---|---|---|")
        models = [(t, res["model"][lab]) for lab, t in LABELS]
        models.append(("C + 시가갭", res["model_with_gap_for_body"]))
        for t, m in models:
            if not m.get("n"):
                continue
            out.append(
                f"| {t} | {m['n']} | {_pct(m['accuracy'])} | {_pct(m['baseline_majority'])} "
                f"| {_pct(m['baseline_prev_direction'])} | {_pct(m['confident_coverage'])} "
                f"| {_pct(m['confident_accuracy'])} |"
            )
        out.append("")
    return "\n".join(out)


# ── 수집 ─────────────────────────────────────────────────────────────

_UA = {"User-Agent": "Mozilla/5.0"}


def fetch_kr_index(symbol: str, count: int) -> List[dict]:
    import httpx

    resp = httpx.get(
        "https://fchart.stock.naver.com/sise.nhn",
        params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
        headers=_UA, timeout=30,
    )
    resp.raise_for_status()
    return parse_naver_fchart(resp.content.decode("euc-kr", errors="replace"))


def fetch_yahoo(symbol: str, years: int) -> List[dict]:
    import httpx

    resp = httpx.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": f"{years + 1}y", "interval": "1d"},
        headers=_UA, timeout=30,
    )
    resp.raise_for_status()
    return parse_yahoo_chart(resp.text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="장전 코스피/코스닥 방향 예측 지표 백테스트")
    p.add_argument("--years", type=int, default=6, help="국내 지수 조회 기간(년)")
    p.add_argument("--min-train", type=int, default=250, help="walk-forward 최소 학습 일수")
    p.add_argument("--refit-every", type=int, default=20, help="재학습 주기(일)")
    p.add_argument("--json", help="결과 JSON 저장 경로")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    us = {f.name: fetch_yahoo(f.symbol, args.years) for f in US_FEATURES}
    results = {}
    for code, _ in KR_INDICES:
        kr = fetch_kr_index(code, count=args.years * 250)
        results[code] = analyze(build_dataset(kr, us), args.min_train, args.refit_every)
    print(format_report(results))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
