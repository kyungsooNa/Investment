"""PointInTimeUniverseProvider — 백테스트 날짜별 상장 종목 질의 계층 (R-1 생존편향).

`point_in_time_universe_service` 가 정규화한 레코드(현재 상장 + 상폐, 각자
listing_date/delisting_date 보유)를 받아, 백테스트의 임의 날짜에 대해
"그 날 상장돼 있던 종목 집합"을 돌려준다. 상폐 종목도 상폐일 직전까지 포함되므로
백테스트 후보군이 생존편향(상폐 종목 누락) 없이 구성될 수 있다.

주의(범위 입력): 기간 백테스트에 쓰려면 입력 레코드가 그 기간 중 한 번이라도
상장돼 있던 모든 종목(기간 중 상폐된 종목 포함)을 담아야 한다. 단일 as-of 날짜로
필터링된 스냅샷(`build_point_in_time_snapshot`)은 그 날짜 이전에 상폐된 종목을
빼므로 기간 백테스트 입력으로는 부족하다 — 정규화된 전체 레코드(current+delisted)를
넘겨야 한다.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from services.point_in_time_universe_service import (
    PointInTimeUniverseRecord,
    _compact_date,
    _is_listed_on,
)

_RECORD_FIELDS = {f for f in PointInTimeUniverseRecord.__dataclass_fields__}


def _to_record(item: Any) -> PointInTimeUniverseRecord | None:
    if isinstance(item, PointInTimeUniverseRecord):
        record = item
    elif isinstance(item, Mapping):
        kwargs = {k: v for k, v in item.items() if k in _RECORD_FIELDS}
        if not kwargs.get("symbol"):
            return None
        record = PointInTimeUniverseRecord(**kwargs)
    else:
        return None
    symbol = _normalize_symbol(record.symbol)
    if not symbol:
        return None
    if symbol != record.symbol:
        # frozen dataclass — 정규화된 symbol 로 교체본 생성
        record = PointInTimeUniverseRecord(
            symbol=symbol,
            name=record.name,
            market=record.market,
            listing_date=record.listing_date,
            delisting_date=record.delisting_date,
            source=record.source,
            secu_group=record.secu_group,
            kind=record.kind,
            reason=record.reason,
        )
    return record


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else text


class PointInTimeUniverseProvider:
    """정규화된 PIT 레코드 집합에 대한 날짜별 상장 종목 질의."""

    def __init__(self, records: Iterable[Any]):
        by_symbol: dict[str, PointInTimeUniverseRecord] = {}
        for item in records:
            record = _to_record(item)
            if record is None:
                continue
            existing = by_symbol.get(record.symbol)
            # 같은 symbol 충돌 시 상폐 이력을 우선(생존편향 방지 — dedupe 정책과 일치)
            if existing is None or (existing.source != "delisted" and record.source == "delisted"):
                by_symbol[record.symbol] = record
        self._by_symbol = by_symbol

    @classmethod
    def from_records_dicts(cls, dicts: Iterable[Mapping[str, Any]]) -> "PointInTimeUniverseProvider":
        return cls(dicts)

    @classmethod
    def from_snapshot_payload(cls, payload: Mapping[str, Any]) -> "PointInTimeUniverseProvider":
        return cls(payload.get("records") or [])

    def listed_codes_as_of(self, date_ymd: str) -> set[str]:
        """date_ymd(YYYYMMDD 또는 YYYY-MM-DD) 시점에 상장돼 있던 종목코드 집합."""
        as_of_compact = _compact_date(date_ymd)
        if not as_of_compact:
            return set()
        return {
            symbol
            for symbol, record in self._by_symbol.items()
            if _is_listed_on(record, as_of_compact)
        }

    def delisted_codes_as_of(self, date_ymd: str) -> set[str]:
        """date_ymd 시점에 상장 중이면서 source=='delisted' 인(=결국 상폐될) 종목코드."""
        as_of_compact = _compact_date(date_ymd)
        if not as_of_compact:
            return set()
        return {
            symbol
            for symbol, record in self._by_symbol.items()
            if record.source == "delisted" and _is_listed_on(record, as_of_compact)
        }

    def all_codes(self) -> set[str]:
        return set(self._by_symbol.keys())

    def record_for(self, code: str) -> PointInTimeUniverseRecord | None:
        return self._by_symbol.get(_normalize_symbol(code))
