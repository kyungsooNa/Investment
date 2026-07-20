"""AI 응답 앞쪽의 '신호: 상|중|하' + '신호 근거:' 줄 파싱 유틸.

AI 종합 분석·뉴스 검토가 첫 줄에 출력하는 신호와 둘째 줄의 근거를 추출해
(신호, 근거, 나머지 본문)으로 분리한다. 응답 본문에 '긍정 신호:'/'위험 신호:'
같은 섹션 제목이 있으므로 줄 시작의 '신호:'만, 앞쪽 몇 줄 안에서만 인식한다.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# '상승세'처럼 값 뒤에 글자가 이어지면 신호로 보지 않는다(\b).
_SIGNAL_LINE_RE = re.compile(r"^[\s>*#\-]*신호\s*[::]\s*\**\s*(상|중|하)\b")
_REASON_LINE_RE = re.compile(r"^[\s>*#\-]*신호\s*근거\s*[::]\s*(.+?)\s*$")
# 프롬프트가 첫 줄 출력을 요구하므로 앞쪽 몇 줄만 본다(본문 오탐 방지).
_SCAN_LINES = 3


def extract_signal(
    text: Optional[str],
) -> Tuple[Optional[str], Optional[str], str]:
    """텍스트 앞쪽의 신호·근거 줄을 찾아 (신호, 근거, 남은 본문)을 반환한다.

    신호 줄이 없으면 (None, None, 원문)을 반환한다. 근거 줄은 신호 줄이
    있을 때만 의미가 있으므로 신호 없이 근거만 있으면 본문을 건드리지 않는다.
    """
    if not text:
        return None, None, ""
    lines = text.splitlines()
    scanned = 0
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        match = _SIGNAL_LINE_RE.match(line)
        if match:
            rest = lines[:idx] + lines[idx + 1:]
            reason, rest = _extract_reason(rest, idx)
            return match.group(1), reason, "\n".join(rest).strip()
        scanned += 1
        if scanned >= _SCAN_LINES:
            break
    return None, None, text


def _extract_reason(
    lines: List[str], start_idx: int
) -> Tuple[Optional[str], List[str]]:
    """신호 줄 바로 뒤의 '신호 근거:' 줄을 찾아 (근거, 그 줄을 뺀 lines)를 반환한다."""
    scanned = 0
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if not line.strip():
            continue
        match = _REASON_LINE_RE.match(line)
        if match:
            reason = match.group(1).strip().strip("*").strip()
            return (reason or None), lines[:idx] + lines[idx + 1:]
        scanned += 1
        if scanned >= 2:
            break
    return None, lines
