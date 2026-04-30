from __future__ import annotations

from datetime import date, datetime
from typing import Any


def normalize_yyyymmdd(value: Any) -> str:
    """날짜/일시 값을 YYYYMMDD 문자열로 정규화한다."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")

    raw = str(value or "").strip()
    if not raw:
        return ""

    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]

    return ""
