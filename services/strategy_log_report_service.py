"""당일 전략 로그 파일을 분석하여 매수 완료/실패 요약 HTML 리포트를 생성한다."""
from __future__ import annotations

import glob
import gzip
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

try:
    import orjson

    def _loads(line: bytes) -> dict:
        return orjson.loads(line)
except ImportError:
    import json

    def _loads(line: bytes) -> dict:
        return json.loads(line)


_STRATEGY_NAME_RE = re.compile(r'^\d{8}_\d{6}_(.+?)(?:\.log\.json.*)$')


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
                    if not entry.get('timestamp', '').startswith(date_prefix):
                        continue
                    data = entry.get('data')
                    if isinstance(data, dict):
                        yield entry.get('level', 'INFO'), data
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

        sections: List[str] = []
        for idx, (name, files) in enumerate(sorted(strategy_files.items()), 1):
            bought: Dict[str, dict] = {}    # code → {name, price, reason}
            rejected: Dict[str, dict] = {}  # code → {name, reason, count}

            for fpath in sorted(files):
                for _level, data in self._iter_events(fpath, date_prefix):
                    event = data.get('event', '')
                    code = data.get('code', '')
                    if not code:
                        continue

                    if event == 'buy_signal_generated':
                        bought[code] = {
                            'name': data.get('name', code),
                            'price': data.get('price', 0),
                            'reason': data.get('reason', ''),
                        }
                        rejected.pop(code, None)

                    elif event in self.REJECTED_EVENTS or event.endswith('_rejected'):
                        if code not in bought:
                            prev = rejected.get(code, {
                                'name': data.get('name', code),
                                'reason': '',
                                'count': 0,
                            })
                            rejected[code] = {
                                'name': data.get('name', code),
                                'reason': data.get('reason', prev['reason']),
                                'count': prev['count'] + 1,
                            }

            lines = [f"<b>{idx}. {name}</b>"]

            if bought:
                lines.append(f"\n✅ 매수 완료 ({len(bought)}건)")
                for code, info in bought.items():
                    price_str = f" @ ₩{info['price']:,}" if info['price'] else ""
                    lines.append(f"• {info['name']}({code}): {info['reason']}{price_str}")
            else:
                lines.append("\n✅ 매수 완료: 없음")

            if rejected:
                lines.append(f"\n❌ 매수 실패 ({len(rejected)}건)")
                for code, info in rejected.items():
                    count_str = f" ({info['count']}회 탈락)" if info['count'] > 1 else ""
                    lines.append(f"• {info['name']}({code}): {info['reason']}{count_str}")
            else:
                lines.append("\n❌ 매수 실패: 없음")

            sections.append("\n".join(lines))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header = f"<b>📊 [{_fmt_date(target_date)}] 전략 실행 요약</b>"
        footer = f"\n\n<i>생성: {now_str}</i>"
        return header + "\n\n" + "\n\n".join(sections) + footer
