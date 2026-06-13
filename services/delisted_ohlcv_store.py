"""DelistedOhlcvStore — 백필된 상폐 종목 OHLCV 서빙 계층 (R-1 생존편향 연결층).

`scripts/backfill_delisted_ohlcv.py` 가 종목별로 저장한 CSV(`{symbol}.csv`,
컬럼 date/symbol/open/high/low/close/volume/change)를 읽어, 백테스트가
`get_recent_daily_ohlcv` 와 동일한 행 스키마(`date`=YYYYMMDD 오름차순,
open/high/low/close=float, volume=int)로 돌려준다.

이 store 는 행(row) 데이터만 돌려주는 순수 소스다. replay_sqs 가 primary
데이터에 없는 상폐 코드를 이 store 로 fallback 시키는 배선은 다음 단계(3단계).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping

from services.point_in_time_universe_service import _compact_date


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    result = _to_float(value)
    return int(result) if result is not None else None


def _normalize_row(raw: Mapping[str, Any]) -> dict | None:
    date = _compact_date(raw.get("date") or raw.get("stck_bsop_date") or "")
    if not date:
        return None
    return {
        "date": date,
        "open": _to_float(raw.get("open")),
        "high": _to_float(raw.get("high")),
        "low": _to_float(raw.get("low")),
        "close": _to_float(raw.get("close")),
        "volume": _to_int(raw.get("volume")),
    }


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


class DelistedOhlcvStore:
    """상폐 종목 일봉을 get_recent_daily_ohlcv 호환 행으로 제공한다."""

    def __init__(self, rows_by_code: Mapping[str, Iterable[Mapping[str, Any]]]):
        normalized: dict[str, list[dict]] = {}
        for code, rows in (rows_by_code or {}).items():
            norm_code = _normalize_code(code)
            if not norm_code:
                continue
            norm_rows = [r for r in (_normalize_row(row) for row in rows) if r is not None]
            norm_rows.sort(key=lambda r: r["date"])
            if norm_rows:
                normalized[norm_code] = norm_rows
        self._rows_by_code = normalized

    @classmethod
    def from_backfill_dir(cls, path: Any) -> "DelistedOhlcvStore":
        directory = Path(path)
        rows_by_code: dict[str, list[dict]] = {}
        if not directory.is_dir():
            return cls(rows_by_code)
        for csv_path in sorted(directory.glob("*.csv")):
            code = _normalize_code(csv_path.stem)
            if not code:
                continue
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
                rows_by_code[code] = list(csv.DictReader(fp))
        return cls(rows_by_code)

    def codes(self) -> set[str]:
        return set(self._rows_by_code.keys())

    def has(self, code: str) -> bool:
        return _normalize_code(code) in self._rows_by_code

    def get_daily_rows(
        self,
        code: str,
        *,
        limit: int | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """code 의 일봉 행(오름차순). end_date(YYYYMMDD/YYYY-MM-DD) 이하만, 최근 limit 개."""
        rows = self._rows_by_code.get(_normalize_code(code))
        if not rows:
            return []
        end_compact = _compact_date(end_date) if end_date else ""
        if end_compact:
            rows = [r for r in rows if r["date"] <= end_compact]
        if limit is not None and limit >= 0:
            rows = rows[-limit:] if limit else []
        return [dict(r) for r in rows]
