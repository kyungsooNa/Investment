"""전략 리포트 공용 포맷 헬퍼.

`strategy_log_report_service` 와 `strategy_execution_quality_report` 가 함께 쓴다.
두 모듈이 서로를 import 하지 않도록 공통 헬퍼만 여기에 둔다(단방향 의존).
"""
from __future__ import annotations

import html
from typing import Any, Optional

# 각 섹션에서 보여줄 최대 행 수
MAX_EXECUTION_QUALITY_ROWS = 5
MAX_CANDIDATE_LIQUIDITY_ROWS = 5


def _esc(value: Any) -> str:
    """HTML 본문 삽입용 텍스트 이스케이프. reason/metric 등에 포함된 '<', '>', '&'가
    Telegram HTML 파서에서 unsupported tag 로 인식되는 것을 방지한다."""
    return html.escape(str(value), quote=False) if value is not None else ""


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_eok_won(value: Any) -> str:
    amount = _to_float(value)
    if amount is None or amount <= 0:
        return "N/A"
    return f"{amount / 100_000_000:.0f}억"


def _first_number(data: dict, *keys: str) -> Optional[float]:
    for key in keys:
        if key in data:
            value = _to_float(data.get(key))
            if value is not None:
                return value
    return None
