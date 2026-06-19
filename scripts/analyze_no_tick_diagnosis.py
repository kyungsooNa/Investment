"""CLI: WebSocket price 피드 무틱(no-tick) 근본 원인 a1/a2 판정기.

P2 2-4 최우선 블로커 진단 도구. 워치독이 `subscribed_no_tick`(=KIS 등록 ACK
확정 후 0틱)으로 표시한 종목을, event_shadow 의 tick-ingest 카운터 스냅샷과
종목별로 조인해 다음으로 분류한다.

  - a1_kis_no_send     : received==0 → ACK 후 KIS 가 프레임 자체를 보내지 않음
                         (todo P2 2-4 "ACK 후 KIS 미전송" 가설 입증)
  - a2_quality_reject  : received>0 & dispatched==0 & quality_reject>0
                         → 프레임은 도착하나 DataQuality 게이트에서 전량 탈락
  - a3_inconsistent    : received>0 & dispatched>0 → 틱이 디스패치됐는데도 워치독이
                         no-tick 으로 표시 (측정/타이밍 갭, 무틱 아님)
  - received_not_dispatched : received>0 & dispatched==0 & quality_reject==0
                         → 프레임 진입했으나 reject/dispatch 어느 쪽도 아님 (희귀)
  - unknown_no_snapshot : no-tick 으로 표시됐으나 tick-ingest 스냅샷에 종목 없음

`not_subscribed`(ACK 미확정) 종목은 무틱과 원인이 다르므로 별도 버킷으로 집계한다.

입력:
  - streaming 로그 (`logs/streaming/*_streaming.log.json`): JsonFormatter 줄 JSON,
    `data.action=="missing_reason"` 의 `data.code` / `data.reason`.
  - event_shadow jsonl (`logs/strategies/event_shadow/YYYYMMDD.jsonl`):
    `event=="subscriptions_refreshed"` 의 `details.tick_ingest[code]` 누적 카운터.
    카운터는 monotonic 누적이므로 종목별 가장 늦은(recorded_at 최대) 스냅샷이 종일 합계.

Examples:
    python scripts/analyze_no_tick_diagnosis.py \
        --streaming-glob "logs/streaming/*_streaming.log.json" \
        --shadow-dir logs/strategies/event_shadow \
        --date-from 20260619 --date-to 20260619 \
        --output-json reports/no_tick_diagnosis.json \
        --output-markdown reports/no_tick_diagnosis.md
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 분류 라벨 (보고서 키 안정성 위해 상수화)
A1 = "a1_kis_no_send"
A2 = "a2_quality_reject"
A3 = "a3_inconsistent"
RECEIVED_NOT_DISPATCHED = "received_not_dispatched"
UNKNOWN_NO_SNAPSHOT = "unknown_no_snapshot"

_CLASSES = [A1, A2, A3, RECEIVED_NOT_DISPATCHED, UNKNOWN_NO_SNAPSHOT]


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_missing_reasons(streaming_paths: List[Path]) -> Dict[str, Dict[str, int]]:
    """streaming 로그에서 종목별 missing_reason 카운트를 집계.

    반환: {code: {"subscribed_no_tick": n, "not_subscribed": m, ...}}
    """
    out: Dict[str, Dict[str, int]] = {}
    for path in streaming_paths:
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
                if data.get("action") != "missing_reason":
                    continue
                code = data.get("code")
                reason = data.get("reason")
                if not code or not reason:
                    continue
                bucket = out.setdefault(code, {})
                bucket[reason] = bucket.get(reason, 0) + 1
    return out


def load_tick_ingest_snapshots(
    shadow_dir: Path,
    date_from: str,
    date_to: str,
    strategy_filter: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """event_shadow jsonl 에서 종목별 최신(누적) tick-ingest 스냅샷을 반환.

    카운터는 monotonic 누적이므로 recorded_at 이 가장 늦은 스냅샷이 종일 합계다.
    반환: {code: {"received":int, "quality_reject":int, "dispatched":int,
                  "recorded_at":float, "strategy":str}}
    """
    shadow_dir = Path(shadow_dir)
    out: Dict[str, Dict[str, Any]] = {}
    if not shadow_dir.exists():
        return out
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
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") != "subscriptions_refreshed":
                    continue
                strategy = rec.get("strategy") or ""
                if strategy_filter and strategy != strategy_filter:
                    continue
                details = rec.get("details") or {}
                tick_ingest = details.get("tick_ingest")
                if not isinstance(tick_ingest, dict):
                    continue
                recorded_at = float(rec.get("recorded_at") or 0.0)
                for code, counts in tick_ingest.items():
                    if not isinstance(counts, dict):
                        continue
                    prev = out.get(code)
                    if prev is not None and prev["recorded_at"] >= recorded_at:
                        continue
                    out[code] = {
                        "received": int(counts.get("received", 0)),
                        "quality_reject": int(counts.get("quality_reject", 0)),
                        "dispatched": int(counts.get("dispatched", 0)),
                        "recorded_at": recorded_at,
                        "strategy": strategy,
                    }
    return out


# ── Classification ───────────────────────────────────────────────────────────

def classify_code(snap: Optional[Dict[str, Any]]) -> str:
    """단일 no-tick 종목을 tick-ingest 스냅샷으로 분류."""
    if snap is None:
        return UNKNOWN_NO_SNAPSHOT
    received = snap.get("received", 0)
    dispatched = snap.get("dispatched", 0)
    quality_reject = snap.get("quality_reject", 0)
    if received == 0:
        return A1
    if dispatched > 0:
        return A3
    if quality_reject > 0:
        return A2
    return RECEIVED_NOT_DISPATCHED


def compute_no_tick_report(
    missing_reasons: Dict[str, Dict[str, int]],
    tick_snaps: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """no-tick 종목 분류 리포트 생성.

    subscribed_no_tick 으로 한 번이라도 표시된 종목만 분류 대상.
    not_subscribed 만 있는 종목은 별도 버킷(ack 미확정)으로 집계.
    """
    no_tick_codes = sorted(
        c for c, r in missing_reasons.items()
        if r.get("subscribed_no_tick", 0) > 0
    )
    not_subscribed_only = sorted(
        c for c, r in missing_reasons.items()
        if r.get("subscribed_no_tick", 0) == 0 and r.get("not_subscribed", 0) > 0
    )

    per_code: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {k: 0 for k in _CLASSES}
    for code in no_tick_codes:
        snap = tick_snaps.get(code)
        cls = classify_code(snap)
        counts[cls] += 1
        entry: Dict[str, Any] = {
            "code": code,
            "classification": cls,
            "subscribed_no_tick_log_count": missing_reasons[code].get("subscribed_no_tick", 0),
        }
        if snap is not None:
            entry["received"] = snap["received"]
            entry["quality_reject"] = snap["quality_reject"]
            entry["dispatched"] = snap["dispatched"]
        per_code.append(entry)

    total = len(no_tick_codes)
    # 우세 가설: 가장 많은 분류 (a1/a2 위주). 동률/0건은 inconclusive.
    verdict = "inconclusive"
    if total > 0:
        top_cls, top_n = max(counts.items(), key=lambda kv: kv[1])
        if top_n > 0:
            verdict = top_cls

    return {
        "summary": {
            "total_no_tick_codes": total,
            "not_subscribed_only_codes": len(not_subscribed_only),
            "classification_counts": counts,
            "verdict": verdict,
        },
        "per_code": per_code,
        "not_subscribed_only": not_subscribed_only,
    }


# ── Reporting ────────────────────────────────────────────────────────────────

_CLASS_DESC = {
    A1: "ACK 후 KIS 프레임 미전송 (received==0)",
    A2: "DataQuality 게이트 전량 탈락 (received>0, dispatched==0)",
    A3: "디스패치됐는데 no-tick 표시 (측정/타이밍 갭, 무틱 아님)",
    RECEIVED_NOT_DISPATCHED: "프레임 진입했으나 reject/dispatch 아님",
    UNKNOWN_NO_SNAPSHOT: "tick-ingest 스냅샷에 종목 없음",
}


def format_markdown_report(report: Dict[str, Any]) -> str:
    s = report["summary"]
    lines: List[str] = []
    lines.append("# WebSocket 무틱(no-tick) 진단 리포트 (P2 2-4)")
    lines.append("")
    lines.append(f"- 무틱(subscribed_no_tick) 종목 수: **{s['total_no_tick_codes']}**")
    lines.append(f"- ACK 미확정(not_subscribed only) 종목 수: {s['not_subscribed_only_codes']}")
    lines.append(f"- 우세 판정: **{s['verdict']}** — {_CLASS_DESC.get(s['verdict'], '판정 불가')}")
    lines.append("")
    lines.append("## 분류 집계")
    lines.append("")
    lines.append("| 분류 | 종목 수 | 설명 |")
    lines.append("|------|---------|------|")
    for cls in _CLASSES:
        lines.append(f"| {cls} | {s['classification_counts'][cls]} | {_CLASS_DESC[cls]} |")
    lines.append("")
    lines.append("## 종목별 상세")
    lines.append("")
    lines.append("| 종목 | 분류 | received | quality_reject | dispatched | no_tick 로그수 |")
    lines.append("|------|------|----------|----------------|------------|----------------|")
    for e in report["per_code"]:
        lines.append(
            f"| {e['code']} | {e['classification']} | "
            f"{e.get('received', '-')} | {e.get('quality_reject', '-')} | "
            f"{e.get('dispatched', '-')} | {e['subscribed_no_tick_log_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WebSocket 무틱 a1/a2 판정기 (P2 2-4)")
    p.add_argument("--streaming-glob", default="logs/streaming/*_streaming.log.json",
                   help="streaming 로그 glob 패턴")
    p.add_argument("--shadow-dir", default="logs/strategies/event_shadow",
                   help="event_shadow jsonl 디렉토리")
    p.add_argument("--date-from", required=True, help="YYYYMMDD (shadow jsonl 필터)")
    p.add_argument("--date-to", required=True, help="YYYYMMDD (shadow jsonl 필터)")
    p.add_argument("--strategy", default="", help="전략명 필터 (생략 시 전체)")
    p.add_argument("--output-json", default="", help="JSON 리포트 출력 경로")
    p.add_argument("--output-markdown", default="", help="Markdown 리포트 출력 경로")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    streaming_paths = [Path(p) for p in sorted(_glob.glob(args.streaming_glob))]
    missing_reasons = load_missing_reasons(streaming_paths)
    tick_snaps = load_tick_ingest_snapshots(
        shadow_dir=Path(args.shadow_dir),
        date_from=args.date_from,
        date_to=args.date_to,
        strategy_filter=args.strategy or None,
    )
    report = compute_no_tick_report(missing_reasons, tick_snaps)
    report["config"] = {
        "streaming_glob": args.streaming_glob,
        "shadow_dir": str(args.shadow_dir),
        "date_from": args.date_from,
        "date_to": args.date_to,
        "strategy": args.strategy,
        "streaming_files": [str(p) for p in streaming_paths],
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
