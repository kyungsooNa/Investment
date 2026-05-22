from __future__ import annotations

from typing import Any, Iterable, Mapping


ENTRY_STATUSES = {"FILLED", "PARTIAL", "SIGNAL"}


def compute_portfolio_entry_pressure_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    daily_entry_warning_threshold: int = 5,
) -> dict[str, Any]:
    """Report whether new portfolio entries cluster on a single day."""
    daily_entries: dict[str, dict[str, Any]] = {}
    total_entry_count = 0

    for record in records:
        if not _is_entry_record(record):
            continue
        date = _record_date(record)
        if not date:
            continue
        strategy = str(record.get("strategy") or "").strip()
        code = str(record.get("code") or "").strip()
        day = daily_entries.setdefault(
            date,
            {"entry_count": 0, "strategies": set(), "codes": set()},
        )
        day["entry_count"] += 1
        total_entry_count += 1
        if strategy:
            day["strategies"].add(strategy)
        if code:
            day["codes"].add(code)

    normalized_daily_entries = {
        date: {
            "entry_count": payload["entry_count"],
            "strategies": sorted(payload["strategies"]),
            "codes": sorted(payload["codes"]),
        }
        for date, payload in sorted(daily_entries.items())
    }
    max_date, max_count = _max_daily_entry(normalized_daily_entries)
    threshold = max(int(daily_entry_warning_threshold or 0), 1)
    warnings = (
        ["portfolio_daily_entry_pressure_high"]
        if max_count >= threshold
        else []
    )

    return {
        "daily_entry_warning_threshold": threshold,
        "total_entry_count": total_entry_count,
        "max_daily_entry_date": max_date,
        "max_daily_entry_count": max_count,
        "daily_entries": normalized_daily_entries,
        "warnings": warnings,
    }


def _is_entry_record(record: Mapping[str, Any]) -> bool:
    status = str(record.get("status") or "").upper()
    side = str(record.get("side") or "").upper()
    if side not in ("", "BUY"):
        return False
    return status in ENTRY_STATUSES


def _record_date(record: Mapping[str, Any]) -> str:
    raw = str(record.get("signal_time") or record.get("date") or "").strip()
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10] if raw else ""


def _max_daily_entry(daily_entries: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, int]:
    if not daily_entries:
        return None, 0
    date, payload = max(
        daily_entries.items(),
        key=lambda item: (int(item[1].get("entry_count") or 0), item[0]),
    )
    return date, int(payload.get("entry_count") or 0)
