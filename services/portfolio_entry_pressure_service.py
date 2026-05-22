from __future__ import annotations

from typing import Any, Iterable, Mapping


ENTRY_STATUSES = {"FILLED", "PARTIAL", "SIGNAL"}


def compute_portfolio_entry_pressure_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    daily_entry_warning_threshold: int = 5,
    opening_entry_warning_threshold: int = 3,
    closing_entry_warning_threshold: int = 3,
) -> dict[str, Any]:
    """Report whether new portfolio entries cluster on a single day."""
    daily_entries: dict[str, dict[str, Any]] = {}
    intraday_entries: dict[str, dict[str, dict[str, Any]]] = {
        "opening": {},
        "closing": {},
    }
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
        window = _record_intraday_window(record)
        if window:
            window_day = intraday_entries[window].setdefault(
                date,
                {"entry_count": 0, "strategies": set(), "codes": set()},
            )
            window_day["entry_count"] += 1
            if strategy:
                window_day["strategies"].add(strategy)
            if code:
                window_day["codes"].add(code)

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
    intraday_windows = {
        "opening": _intraday_window_summary(
            intraday_entries["opening"],
            threshold=opening_entry_warning_threshold,
        ),
        "closing": _intraday_window_summary(
            intraday_entries["closing"],
            threshold=closing_entry_warning_threshold,
        ),
    }
    warnings = []
    if max_count >= threshold:
        warnings.append("portfolio_daily_entry_pressure_high")
    if intraday_windows["opening"]["warning"]:
        warnings.append("portfolio_opening_entry_pressure_high")
    if intraday_windows["closing"]["warning"]:
        warnings.append("portfolio_closing_entry_pressure_high")

    return {
        "daily_entry_warning_threshold": threshold,
        "total_entry_count": total_entry_count,
        "max_daily_entry_date": max_date,
        "max_daily_entry_count": max_count,
        "daily_entries": normalized_daily_entries,
        "intraday_windows": intraday_windows,
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


def _record_intraday_window(record: Mapping[str, Any]) -> str | None:
    time_text = _record_time(record)
    if not time_text:
        return None
    if "09:00:00" <= time_text < "10:00:00":
        return "opening"
    if time_text >= "14:30:00":
        return "closing"
    return None


def _record_time(record: Mapping[str, Any]) -> str:
    raw = str(record.get("signal_time") or "").strip()
    if "T" in raw:
        raw = raw.replace("T", " ")
    parts = raw.split()
    if len(parts) < 2:
        return ""
    time_part = parts[1].split(".")[0]
    chunks = time_part.split(":")
    if len(chunks) < 2:
        return ""
    hour = chunks[0].zfill(2)
    minute = chunks[1].zfill(2)
    second = chunks[2].zfill(2) if len(chunks) > 2 else "00"
    return f"{hour}:{minute}:{second}"


def _max_daily_entry(daily_entries: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, int]:
    if not daily_entries:
        return None, 0
    date, payload = max(
        daily_entries.items(),
        key=lambda item: (int(item[1].get("entry_count") or 0), item[0]),
    )
    return date, int(payload.get("entry_count") or 0)


def _intraday_window_summary(
    entries: Mapping[str, Mapping[str, Any]],
    *,
    threshold: int,
) -> dict[str, Any]:
    normalized_entries = {
        date: {
            "entry_count": payload["entry_count"],
            "strategies": sorted(payload["strategies"]),
            "codes": sorted(payload["codes"]),
        }
        for date, payload in sorted(entries.items())
    }
    max_date, max_count = _max_daily_entry(normalized_entries)
    entry_threshold = max(int(threshold or 0), 1)
    return {
        "entry_warning_threshold": entry_threshold,
        "max_entry_date": max_date,
        "max_entry_count": max_count,
        "daily_entries": normalized_entries,
        "warning": max_count >= entry_threshold,
    }
