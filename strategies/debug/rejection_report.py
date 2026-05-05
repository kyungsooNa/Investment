from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import List

from strategies.debug.rejection_collector import RejectionEvent
from strategies.debug.strategy_debug_runner import DebugReport

_STAGE_LABELS = {
    "pp_rejected": "PP 조건",
    "bgu_rejected": "BGU 조건",
    "entry_rejected": "체결강도/공통 조건",
    "entry_rejected_by_smart_money": "스마트머니(최종)",
    "smart_money_rejected": "스마트머니 세부",
    "cgld_check_failed": "체결강도 조회 오류",
    "scan_skipped": "스캔 스킵",
    "buy_signal_generated": "매수 신호 발생",
    "stage_blocked": "StageGuard",
}


def _event_label(event: RejectionEvent) -> str:
    label = _STAGE_LABELS.get(event.event, event.event)
    reason = event.details.get("reason", "")
    d = event.details

    if event.event == "entry_rejected" and reason == "low_execution_strength":
        entry_type = d.get("entry_type", "?")
        cgld = d.get("cgld", "?")
        thr = d.get("threshold", "?")
        return f"{label} 탈락 [{entry_type}] — cgld={cgld} < {thr}"

    if event.event == "stage_blocked":
        return f"{label} 탈락 — stage={d.get('stage', '?')}"

    if reason == "poor_candle_quality":
        pos = d.get("pos", "?")
        thr = d.get("threshold", "?")
        return f"{label} 탈락 — {reason} (pos={pos} < {thr})"

    if reason == "no_ma_proximity":
        closest = d.get("closest_ma_pct")
        closest_str = f"{closest:+.2f}%" if isinstance(closest, (int, float)) else str(closest)
        return f"{label} 탈락 — {reason} (최근접MA={closest_str})"

    if reason == "insufficient_volume":
        proj = d.get("proj_vol", "?")
        thr = d.get("threshold", "?")
        fmt = lambda v: f"{v:,}" if isinstance(v, int) else str(v)
        return f"{label} 탈락 — {reason} (예상={fmt(proj)} < {fmt(thr)})"

    if reason == "low_pg_ratio":
        pg_tv = d.get("pg_to_tv_pct", "?")
        thr = d.get("threshold", "?")
        return f"{label} 탈락 — {reason} (pg_tv={pg_tv}% < {thr}%)"

    if reason == "low_pg_metrics":
        pg_tv = d.get("pg_tv_pct", "?")
        pg_mc = d.get("pg_mc_pct", "?")
        mc_thr = d.get("mc_threshold", "?")
        cgld = d.get("cgld", "?")
        return f"{label} 탈락 — {reason} (pg_tv={pg_tv}% pg_mc={pg_mc}% < {mc_thr}% cgld={cgld})"

    if reason:
        return f"{label} 탈락 — {reason}"
    return label


def format_console(report: DebugReport) -> str:
    lines: List[str] = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"\n{'='*60}")
    lines.append(f"  전략 디버깅 리포트: {report.strategy_name}  ({ts})")
    lines.append(f"{'='*60}")

    # 요청/스캔/누락 종목 분리 표시
    if report.requested_codes is not None:
        lines.append(f"\n[요청 종목]  {', '.join(report.requested_codes) or '없음'}")
        lines.append(f"[스캔 대상]  {', '.join(report.scanned_codes) or '없음'}")
        if report.missing_codes:
            lines.append(f"[NOT_IN_WATCHLIST]  {', '.join(report.missing_codes)}")
            lines.append("  → 위 종목은 universe watchlist에 없어 스캔되지 않았습니다.")
    else:
        lines.append("\n[스캔 대상]  universe 전체")

    # 종목별 필터 결과 표
    by_code: dict = {}
    for e in report.events:
        by_code.setdefault(e.code, []).append(e)

    signal_codes = {s.code for s in report.signals}
    all_codes = list(dict.fromkeys(
        report.scanned_codes + [e.code for e in report.events if e.code]
    ))

    lines.append(
        f"\n[종목별 필터 결과] "
        f"({len(report.scanned_codes)}종목 스캔, {len(report.signals)}개 신호)"
    )
    lines.append(f"{'종목코드':<10} {'결과':<12} 상세")
    lines.append("-" * 60)
    for code in all_codes:
        events = by_code.get(code, [])
        if code in signal_codes:
            lines.append(f"{code:<10} {'✓ 신호':<12}")
        elif events:
            last = events[-1]
            lines.append(f"{code:<10} {'✗ 탈락':<12} {_event_label(last)}")
        else:
            lines.append(
                f"{code:<10} {'- 정보없음':<12} "
                "(이벤트 미캡처 — 로그 미출력 가능)"
            )

    # 탈락 사유 Top 5
    reasons = [e.reason for e in report.events if e.event != "buy_signal_generated"]
    if reasons:
        lines.append("\n[탈락 사유 Top 5]")
        for reason, cnt in Counter(reasons).most_common(5):
            lines.append(f"  {cnt:>3}회  {reason}")

    # 추론 한계 표시
    if report.limitations:
        lines.append("\n[주의 — 추론 한계]")
        for lim in report.limitations:
            lines.append(f"  * {lim}")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_json(report: DebugReport) -> str:
    def _serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Not JSON serializable: {type(obj)}")

    data = {
        "strategy_name": report.strategy_name,
        "requested_codes": report.requested_codes,
        "scanned_codes": report.scanned_codes,
        "missing_codes": report.missing_codes,
        "signals": [
            {
                "code": s.code,
                "name": getattr(s, "name", ""),
                "action": s.action,
                "price": s.price,
            }
            for s in report.signals
        ],
        "events": [
            {
                "event": e.event,
                "code": e.code,
                "reason": e.reason,
                "details": e.details,
                "timestamp": e.timestamp,
                "level": e.level,
            }
            for e in report.events
        ],
        "limitations": report.limitations,
        "journal_records": report.journal_records,
    }
    return json.dumps(data, ensure_ascii=False, indent=2, default=_serialize)
