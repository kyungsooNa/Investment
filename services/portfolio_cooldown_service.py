from __future__ import annotations

from typing import Any, Iterable, Mapping


def compute_portfolio_cooldown_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    consecutive_loss_warning_threshold: int = 3,
) -> dict[str, Any]:
    """Report strategies that may need a cooldown after consecutive losses."""
    threshold = max(int(consecutive_loss_warning_threshold or 0), 1)
    grouped = _sold_records_by_strategy(records)
    strategies: dict[str, dict[str, Any]] = {}

    for strategy, items in sorted(grouped.items()):
        strategies[strategy] = _strategy_loss_streak_summary(strategy, items)

    candidates = [
        {
            "strategy": strategy,
            "max_consecutive_losses": payload["max_consecutive_losses"],
            "current_consecutive_losses": payload["current_consecutive_losses"],
            "latest_loss_date": payload["latest_loss_date"],
            "total_loss_count": payload["total_loss_count"],
        }
        for strategy, payload in strategies.items()
        if int(payload["current_consecutive_losses"]) >= threshold
    ]
    candidates.sort(
        key=lambda item: (
            -int(item["max_consecutive_losses"]),
            -int(item["current_consecutive_losses"]),
            item["strategy"],
        )
    )

    return {
        "consecutive_loss_warning_threshold": threshold,
        "strategy_count": len(strategies),
        "strategies": strategies,
        "candidates": candidates,
        "warnings": (
            ["portfolio_consecutive_loss_cooldown_candidate"]
            if candidates
            else []
        ),
    }


def _sold_records_by_strategy(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        if str(record.get("status") or "").upper() != "SOLD":
            continue
        strategy = str(record.get("strategy") or "").strip()
        if not strategy:
            continue
        grouped.setdefault(strategy, []).append(record)
    return grouped


def _strategy_loss_streak_summary(
    strategy: str,
    records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    current = 0
    best = 0
    latest_loss_date: str | None = None
    total_loss_count = 0
    ordered = sorted(records, key=_closed_time_key)

    for record in ordered:
        if _is_loss(record):
            current += 1
            total_loss_count += 1
            best = max(best, current)
            latest_loss_date = _record_date(record)
        else:
            current = 0

    return {
        "strategy": strategy,
        "trade_count": len(ordered),
        "total_loss_count": total_loss_count,
        "max_consecutive_losses": best,
        "current_consecutive_losses": current,
        "latest_loss_date": latest_loss_date,
    }


def _is_loss(record: Mapping[str, Any]) -> bool:
    net_pnl = _to_float(record.get("net_pnl"))
    if net_pnl is not None:
        return net_pnl < 0
    net_return = _to_float(record.get("net_return"))
    return bool(net_return is not None and net_return < 0)


def _closed_time_key(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("sell_date", "exit_time", "closed_at"):
            value = metadata.get(key)
            if value not in (None, ""):
                return str(value)
    for key in ("sell_date", "exit_time", "closed_at", "signal_time"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _record_date(record: Mapping[str, Any]) -> str:
    raw = _closed_time_key(record)
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10] if raw else ""


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
