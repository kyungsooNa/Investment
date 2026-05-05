"""Backtest-vs-live comparison helpers for normalized trade journal records."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable


def compare_trade_journals(
    backtest_records: Iterable[dict[str, Any]],
    live_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare normalized backtest and live/paper trade journal records.

    Records are paired by strategy, code, and signal date. Multiple records for
    the same key are matched in signal_time order so intraday re-entry cases can
    still be compared deterministically.
    """
    backtest = sorted(list(backtest_records), key=_sort_key)
    live = sorted(list(live_records), key=_sort_key)
    live_by_key: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for record in live:
        live_by_key[_match_key(record)].append(record)

    matches: list[dict[str, Any]] = []
    unmatched_backtest: list[dict[str, Any]] = []
    matched_live_ids: set[int] = set()

    for backtest_record in backtest:
        key = _match_key(backtest_record)
        candidates = live_by_key.get(key)
        if not candidates:
            unmatched_backtest.append(backtest_record)
            continue
        live_record = candidates.popleft()
        matched_live_ids.add(id(live_record))
        matches.append(_comparison_row(backtest_record, live_record))

    unmatched_live = [record for record in live if id(record) not in matched_live_ids]

    return {
        "summary": _summary(backtest, live, matches, unmatched_backtest, unmatched_live),
        "matches": matches,
        "unmatched_backtest": unmatched_backtest,
        "unmatched_live": unmatched_live,
    }


def _comparison_row(backtest: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    backtest_net_return = _to_float(backtest.get("net_return"))
    live_net_return = _to_float(live.get("net_return"))
    backtest_net_pnl = _to_float(backtest.get("net_pnl"))
    live_net_pnl = _to_float(live.get("net_pnl"))
    backtest_fill_price = _to_float(backtest.get("fill_price"))
    live_fill_price = _to_float(live.get("fill_price"))

    return {
        "strategy": str(backtest.get("strategy") or live.get("strategy") or ""),
        "code": str(backtest.get("code") or live.get("code") or ""),
        "trade_date": _trade_date(backtest) or _trade_date(live),
        "backtest_signal_time": str(backtest.get("signal_time") or ""),
        "live_signal_time": str(live.get("signal_time") or ""),
        "backtest_net_return": backtest_net_return,
        "live_net_return": live_net_return,
        "net_return_diff": _round_or_none(_diff(live_net_return, backtest_net_return)),
        "backtest_net_pnl": backtest_net_pnl,
        "live_net_pnl": live_net_pnl,
        "net_pnl_diff": _round_or_none(_diff(live_net_pnl, backtest_net_pnl)),
        "backtest_fill_price": backtest_fill_price,
        "live_fill_price": live_fill_price,
        "fill_price_diff_pct": _round_or_none(_pct_diff(live_fill_price, backtest_fill_price), digits=4),
        "backtest": backtest,
        "live": live,
    }


def _summary(
    backtest: list[dict[str, Any]],
    live: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    unmatched_backtest: list[dict[str, Any]],
    unmatched_live: list[dict[str, Any]],
) -> dict[str, Any]:
    net_return_diffs = [row["net_return_diff"] for row in matches if row["net_return_diff"] is not None]
    fill_price_diffs = [row["fill_price_diff_pct"] for row in matches if row["fill_price_diff_pct"] is not None]
    net_pnl_diffs = [row["net_pnl_diff"] for row in matches if row["net_pnl_diff"] is not None]

    return {
        "backtest_count": len(backtest),
        "live_count": len(live),
        "matched_count": len(matches),
        "unmatched_backtest_count": len(unmatched_backtest),
        "unmatched_live_count": len(unmatched_live),
        "avg_net_return_diff": _avg(net_return_diffs),
        "avg_abs_net_return_diff": _avg([abs(v) for v in net_return_diffs]),
        "avg_fill_price_diff_pct": _avg(fill_price_diffs, digits=4),
        "total_net_pnl_diff": _round_or_none(sum(net_pnl_diffs)) if net_pnl_diffs else None,
    }


def _match_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("strategy") or ""),
        str(record.get("code") or ""),
        _trade_date(record),
    )


def _sort_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    strategy, code, date = _match_key(record)
    return strategy, code, date, str(record.get("signal_time") or "")


def _trade_date(record: dict[str, Any]) -> str:
    raw = str(record.get("signal_time") or "").strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _pct_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return (left - right) / right * 100


def _avg(values: list[float], *, digits: int = 4) -> float | None:
    if not values:
        return None
    return _round_or_none(sum(values) / len(values), digits=digits)


def _round_or_none(value: float | None, *, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)
