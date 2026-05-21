from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional


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


def previous_trading_day_str(
    now: datetime | date,
    holidays: Optional[Iterable[str]] = None,
) -> str:
    """주어진 시점의 직전 영업일을 YYYYMMDD 문자열로 반환한다.

    - 주말(토/일)을 건너뛴다.
    - ``holidays``(YYYYMMDD 문자열 집합)가 주어지면 추가로 건너뛴다.
    - ``MarketCalendarService`` 가 주입되지 않은 호출 경로에서도 안전하게
      "전일까지 확정 OHLCV" 의 ``end_date`` 를 구하기 위한 sync helper.
    """
    base = now.date() if isinstance(now, datetime) else now
    holiday_set = set(holidays) if holidays else set()
    check = base - timedelta(days=1)
    for _ in range(30):
        ds = check.strftime("%Y%m%d")
        if check.weekday() < 5 and ds not in holiday_set:
            return ds
        check -= timedelta(days=1)
    # 안전망 — 30일 연속 휴장은 실질적으로 발생하지 않으나 호출자가 무한 루프를 보지 않도록 보장한다.
    return check.strftime("%Y%m%d")
