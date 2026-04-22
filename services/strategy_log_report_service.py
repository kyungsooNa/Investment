"""당일 전략 로그 파일을 분석하여 매수 완료/실패 요약 HTML 리포트를 생성한다."""
from __future__ import annotations

import glob
import gzip
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import orjson

    def _loads(line: bytes) -> dict:
        return orjson.loads(line)
except ImportError:
    import json

    def _loads(line: bytes) -> dict:
        return json.loads(line)


_STRATEGY_NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+?)(?:\.log\.json.*)$')

# 매수 근접 추적 상수 ─────────────────────────────────────────────────

_NEAR_MISS_EVENTS = frozenset({
    "htf_pattern_detected",
    "breakout_rejected", "pp_rejected", "entry_rejected",
})

# 높을수록 매수에 가까운 단계에서 탈락 (최대 8)
_GATE_PRIORITY: Dict[Tuple[str, str], int] = {
    ("htf_pattern_detected",  ""):                       8,
    ("breakout_rejected",     "smart_money_filter_failed"): 7,
    ("breakout_rejected",     "low_execution_strength"):  6,
    ("entry_rejected",        "low_execution_strength"):  6,
    ("breakout_rejected",     "insufficient_projected_volume"): 5,
    ("pp_rejected",           "insufficient_volume"):     5,
    ("entry_rejected",        "no_bullish_reversal"):     5,
    ("breakout_rejected",     "poor_candle_quality"):     4,
    ("pp_rejected",           "no_ma_proximity"):         2,
}

_REASON_KR: Dict[str, str] = {
    "smart_money_filter_failed":     "수급 미달",
    "low_execution_strength":        "체결강도 미달",
    "insufficient_projected_volume": "거래량 미달",
    "insufficient_volume":           "거래량 미달",
    "no_bullish_reversal":           "반등 미확인",
    "poor_candle_quality":           "캔들 위치 미달",
    "no_ma_proximity":               "MA 거리 초과",
}


def _build_metric_str(event: str, reason: str, data: dict) -> str:
    """rejection 데이터에서 핵심 수치 문자열을 반환한다."""
    if event == "htf_pattern_detected":
        return f"폭등 {data.get('surge_ratio', 0):.1f}x, 깃발 {data.get('flag_days', 0)}일"
    if reason == "low_execution_strength":
        cgld = data.get('cgld', 0)
        thr = data.get('threshold', 0)
        return f"강도 {cgld:.1f}%/기준 {thr:.0f}%" if thr else f"강도 {cgld:.1f}%"
    if reason == "poor_candle_quality":
        return f"위치 {data.get('pos', 0):.2f}"
    if reason == "no_ma_proximity":
        pct = data.get('closest_ma_pct')
        return f"MA 거리 {pct:+.2f}%" if pct is not None else ""
    if reason in ("insufficient_volume", "insufficient_projected_volume"):
        pv = data.get('proj_vol', 0)
        thr = data.get('threshold', 0)
        return f"예상거래 {int(pv):,}/기준 {int(thr):,}" if thr else ""
    return ""


def _extract_strategy_name(filepath: str) -> Optional[str]:
    """파일명에서 전략 이름을 추출한다.

    예: '20260418_093000_OneilSqueezeBreakout.log.json' → 'OneilSqueezeBreakout'
    """
    m = _STRATEGY_NAME_RE.match(os.path.basename(filepath))
    return m.group(1) if m else None


def _fmt_date(date_str: str) -> str:
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


class StrategyLogReportService:
    """당일 전략 로그 파일을 분석하여 매수 완료/실패 요약 HTML 리포트를 생성한다.

    logs/strategies/ 디렉토리에서 최근 48시간 내 수정된 *.log.json* 파일을
    전략별로 그룹화하고, target_date에 해당하는 이벤트만 추려 리포트를 생성한다.
    """

    REJECTED_EVENTS = frozenset({
        "breakout_rejected", "pp_rejected", "htf_rejected", "fp_rejected",
        "entry_rejected",
    })

    def __init__(self, log_dir: str = "logs/strategies"):
        self._log_dir = log_dir

    # ── 파일 탐색 ────────────────────────────────────────────────

    def _find_strategy_files(self) -> Dict[str, List[str]]:
        """최근 48시간 내 수정된 전략 로그 파일을 전략 이름별로 그룹화한다.

        하위 디렉토리(예: logs/strategies/oneil/)도 재귀 탐색한다.
        """
        cutoff = time.time() - 48 * 3600
        pattern = os.path.join(self._log_dir, "**", "*.log.json*")
        result: Dict[str, List[str]] = {}
        for fpath in glob.glob(pattern, recursive=True):
            if os.path.getmtime(fpath) < cutoff:
                continue
            name = _extract_strategy_name(fpath)
            if name:
                result.setdefault(name, []).append(fpath)
        return result

    # ── 로그 파싱 ────────────────────────────────────────────────

    def _iter_events(self, path: str, date_prefix: str):
        """로그 파일을 읽어 date_prefix로 시작하는 타임스탬프의 이벤트를 yield한다."""
        open_fn = gzip.open if path.endswith('.gz') else open
        try:
            with open_fn(path, 'rb') as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = _loads(raw)
                    except Exception:
                        continue
                    ts = entry.get('timestamp', '')
                    if not ts.startswith(date_prefix):
                        continue
                    data = entry.get('data')
                    if isinstance(data, dict):
                        yield entry.get('level', 'INFO'), ts, data
        except Exception:
            pass

    # ── 리포트 생성 ──────────────────────────────────────────────

    async def generate_report(self, target_date: str) -> str:
        """
        target_date: "YYYYMMDD" 형식
        전략별 매수 완료/실패를 분석한 HTML 리포트 문자열을 반환한다.
        """
        date_prefix = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
        strategy_files = self._find_strategy_files()

        if not strategy_files:
            return (
                f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>\n\n"
                "당일 전략 로그가 없습니다."
            )

        active_sections: List[str] = []
        inactive_names: List[str] = []
        market_timing: Dict[str, bool] = {}
        idx = 0

        for name, files in sorted(strategy_files.items()):
            bought: Dict[str, dict] = {}
            rejected: Dict[str, dict] = {}
            scan_count: int = 0
            near_miss: Dict[str, dict] = {}
            name_map: Dict[str, str] = {}

            for fpath in sorted(files):
                for _level, ts, data in self._iter_events(fpath, date_prefix):
                    event = data.get('event', '')
                    code = data.get('code', '')

                    if code and data.get('name'):
                        name_map[code] = data['name']

                    if event == 'scan_with_watchlist':
                        scan_count = max(scan_count, data.get('count', 0))

                    elif event == 'market_timing_updated':
                        mkt = data.get('market', '')
                        if mkt and mkt not in market_timing:
                            market_timing[mkt] = data.get('ok', False)

                    elif event == 'buy_signal_generated' and code:
                        metrics = data.get('metrics', {})
                        price = metrics.get('price', data.get('price', 0))
                        bought[code] = {
                            'name': data.get('name', code),
                            'price': price,
                            'reason': data.get('reason', ''),
                            'time': ts[11:16] if len(ts) >= 16 else '',
                        }
                        rejected.pop(code, None)
                        near_miss.pop(code, None)

                    elif code and (event in self.REJECTED_EVENTS or event.endswith('_rejected')):
                        if code not in bought:
                            prev = rejected.get(code, {'name': data.get('name', code), 'reason': '', 'count': 0})
                            rejected[code] = {
                                'name': data.get('name', code),
                                'reason': data.get('reason', prev['reason']),
                                'count': prev['count'] + 1,
                            }

                    if event in _NEAR_MISS_EVENTS and code and code not in bought:
                        reason = data.get('reason', '')
                        gate = _GATE_PRIORITY.get((event, reason), 0)
                        if gate > 0 and gate > near_miss.get(code, {}).get('gate', -1):
                            near_miss[code] = {
                                'name': name_map.get(code, data.get('name', code)),
                                'gate': gate,
                                'reason_kr': _REASON_KR.get(reason, "HTF 패턴 감지" if event == "htf_pattern_detected" else reason),
                                'metric_str': _build_metric_str(event, reason, data),
                            }

            if not bought and not rejected and not near_miss:
                inactive_names.append(name)
                continue

            idx += 1
            scan_str = f" — {scan_count}종목 스캔" if scan_count else ""
            lines = [f"<b>{idx}. {name}</b>{scan_str}"]

            if bought:
                lines.append(f"\n✅ 매수 완료 ({len(bought)}건)")
                for code, info in bought.items():
                    price_str = f" @ ₩{info['price']:,}" if info['price'] else ""
                    time_str = f" ({info['time']})" if info.get('time') else ""
                    lines.append(f"• {info['name']}({code}): {info['reason']}{price_str}{time_str}")
            else:
                lines.append("\n✅ 매수 완료: 없음")

            if rejected:
                lines.append(f"\n❌ 매수 실패 ({len(rejected)}건)")
                for code, info in rejected.items():
                    count_str = f" ({info['count']}회 탈락)" if info.get('count', 0) > 1 else ""
                    lines.append(f"• {info['name']}({code}): {info['reason']}{count_str}")
            else:
                lines.append("\n❌ 매수 실패: 없음")

            top3 = sorted(near_miss.values(), key=lambda x: -x['gate'])[:3]
            if top3:
                lines.append("\n🎯 매수 근접")
                for c in top3:
                    metric = f" ({c['metric_str']})" if c['metric_str'] else ""
                    lines.append(f"• {c['name']}: {c['reason_kr']}{metric}")

            active_sections.append("\n".join(lines))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if market_timing:
            mt_parts = [
                f"{mkt} {'🟢' if market_timing[mkt] else '🔴'}"
                for mkt in ["KOSPI", "KOSDAQ"] if mkt in market_timing
            ]
            header = (
                f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>\n"
                f"<i>시장: {' | '.join(mt_parts)} (MA20 추세)</i>"
            )
        else:
            header = f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>"

        footer = f"\n\n<i>생성: {now_str}</i>"

        if not active_sections:
            return header + "\n\n당일 활동한 전략이 없습니다." + footer

        body = "\n\n".join(active_sections)

        if inactive_names:
            inactive_summary = f"\n\n💤 <i>활동 없음: {', '.join(inactive_names[:3])}"
            if len(inactive_names) > 3:
                inactive_summary += f" 외 {len(inactive_names) - 3}개"
            inactive_summary += "</i>"
            body += inactive_summary

        return header + "\n\n" + body + footer
