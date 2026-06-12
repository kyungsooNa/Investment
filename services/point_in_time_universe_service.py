from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


_MARKET_ID_MAP = {
    "STK": "KOSPI",
    "KSQ": "KOSDAQ",
    "KNX": "KONEX",
}


@dataclass(frozen=True)
class PointInTimeUniverseRecord:
    symbol: str
    name: str
    market: str
    listing_date: str = ""
    delisting_date: str = ""
    source: str = "current"
    secu_group: str = ""
    kind: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    ts = pd.to_datetime(value, errors="coerce")
    if not pd.isna(ts):
        return ts.strftime("%Y-%m-%d")
    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def _compact_date(value: Any) -> str:
    normalized = _normalize_date(value)
    digits = re.sub(r"\D", "", normalized)
    return digits[:8] if len(digits) >= 8 else ""


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and not pd.isna(row[name]):
            return row[name]
    return ""


def _normalize_symbol(value: Any) -> str:
    text = _text(value)
    if text.isdigit():
        return text.zfill(6)
    return text


def _normalize_market(row: Mapping[str, Any]) -> str:
    market = _text(_pick(row, "market", "Market", "시장구분"))
    if market:
        return market
    market_id = _text(_pick(row, "MarketId", "market_id"))
    return _MARKET_ID_MAP.get(market_id, market_id)


def _is_spac(record: PointInTimeUniverseRecord) -> bool:
    return "스팩" in record.name or "스팩" in record.reason


def normalize_current_listings(df: pd.DataFrame) -> list[PointInTimeUniverseRecord]:
    records: list[PointInTimeUniverseRecord] = []
    if df is None or df.empty:
        return records
    for raw in df.to_dict("records"):
        symbol = _normalize_symbol(_pick(raw, "symbol", "Symbol", "Code", "종목코드"))
        if not symbol:
            continue
        records.append(
            PointInTimeUniverseRecord(
                symbol=symbol,
                name=_text(_pick(raw, "name", "Name", "종목명")),
                market=_normalize_market(raw),
                listing_date=_normalize_date(_pick(raw, "listing_date", "ListingDate", "상장일")),
                source="current",
                secu_group=_text(_pick(raw, "secu_group", "SecuGroup")),
                kind=_text(_pick(raw, "kind", "Kind")),
            )
        )
    return records


def normalize_delisted_listings(
    rows: Iterable[Mapping[str, Any]],
) -> list[PointInTimeUniverseRecord]:
    records: list[PointInTimeUniverseRecord] = []
    for raw in rows:
        symbol = _normalize_symbol(_pick(raw, "symbol", "Symbol", "Code", "종목코드"))
        if not symbol:
            continue
        records.append(
            PointInTimeUniverseRecord(
                symbol=symbol,
                name=_text(_pick(raw, "name", "Name", "종목명")),
                market=_normalize_market(raw),
                listing_date=_normalize_date(_pick(raw, "listing_date", "ListingDate", "상장일")),
                delisting_date=_normalize_date(_pick(raw, "delisting_date", "DelistingDate", "상장폐지일")),
                source="delisted",
                secu_group=_text(_pick(raw, "secu_group", "SecuGroup")),
                kind=_text(_pick(raw, "kind", "Kind")),
                reason=_text(_pick(raw, "reason", "Reason", "상장폐지사유")),
            )
        )
    return records


def _is_listed_on(record: PointInTimeUniverseRecord, as_of_compact: str) -> bool:
    listing_compact = _compact_date(record.listing_date)
    delisting_compact = _compact_date(record.delisting_date)
    if listing_compact and listing_compact > as_of_compact:
        return False
    # Delisted stocks are included through the day before delisting.
    if delisting_compact and as_of_compact >= delisting_compact:
        return False
    return True


def _dedupe_preferring_history(
    records: Iterable[PointInTimeUniverseRecord],
) -> list[PointInTimeUniverseRecord]:
    by_symbol: dict[str, PointInTimeUniverseRecord] = {}
    for record in records:
        existing = by_symbol.get(record.symbol)
        if existing is None:
            by_symbol[record.symbol] = record
            continue
        if existing.source != "delisted" and record.source == "delisted":
            by_symbol[record.symbol] = record
    return list(by_symbol.values())


def build_point_in_time_snapshot(
    records: Iterable[PointInTimeUniverseRecord],
    as_of_date: str,
    *,
    markets: Sequence[str] = ("KOSPI", "KOSDAQ"),
    exclude_spac: bool = False,
) -> list[PointInTimeUniverseRecord]:
    as_of_compact = _compact_date(as_of_date)
    if not as_of_compact:
        raise ValueError("as_of_date must be YYYYMMDD or YYYY-MM-DD")

    market_set = set(markets or [])
    snapshot = []
    for record in _dedupe_preferring_history(records):
        if market_set and record.market not in market_set:
            continue
        if exclude_spac and _is_spac(record):
            continue
        if not _is_listed_on(record, as_of_compact):
            continue
        snapshot.append(record)
    snapshot.sort(key=lambda r: r.symbol)
    return snapshot


def compare_current_to_point_in_time(
    current_records: Iterable[PointInTimeUniverseRecord],
    point_in_time_records: Iterable[PointInTimeUniverseRecord],
) -> dict[str, Any]:
    current_by_symbol = {r.symbol: r for r in current_records}
    pit_by_symbol = {r.symbol: r for r in point_in_time_records}

    delisted_only = [
        r for symbol, r in pit_by_symbol.items()
        if symbol not in current_by_symbol and r.source == "delisted"
    ]
    current_only = [
        r for symbol, r in current_by_symbol.items()
        if symbol not in pit_by_symbol
    ]
    delisted_only.sort(key=lambda r: r.symbol)
    current_only.sort(key=lambda r: r.symbol)
    return {
        "current_count": len(current_by_symbol),
        "point_in_time_count": len(pit_by_symbol),
        "delisted_only_count": len(delisted_only),
        "current_only_count": len(current_only),
        "delisted_only": [r.to_dict() for r in delisted_only],
        "current_only": [r.to_dict() for r in current_only],
    }
