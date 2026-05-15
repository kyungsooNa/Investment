"""Volatility helpers for journal/report instrumentation.

Reports use 20-day close-to-close log-return standard deviation, annualized
with sqrt(252). Hard gate 도입 전 단계로 record metadata/standard field 로
보존해 regime 버킷과 결합 분석을 가능하게 한다.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


TRADING_DAYS_PER_YEAR = 252
DEFAULT_LOOKBACK = 20


def _coerce_closes(closes: Iterable[float | int | None]) -> list[float]:
    cleaned: list[float] = []
    for value in closes:
        if value is None:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(num) or num <= 0:
            continue
        cleaned.append(num)
    return cleaned


def annualized_return_std(
    closes: Sequence[float | int | None],
    *,
    lookback: int = DEFAULT_LOOKBACK,
) -> float | None:
    """Annualized stdev of close-to-close log returns over the last `lookback` days.

    Returns None when fewer than `lookback` valid log returns can be formed
    (i.e. fewer than `lookback + 1` non-null positive closes).
    """
    if lookback <= 1:
        raise ValueError("lookback must be >= 2")

    cleaned = _coerce_closes(closes)
    if len(cleaned) < lookback + 1:
        return None

    tail = cleaned[-(lookback + 1) :]
    log_returns = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    n = len(log_returns)
    if n < 2:
        return None

    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    if variance < 0:
        return None
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
