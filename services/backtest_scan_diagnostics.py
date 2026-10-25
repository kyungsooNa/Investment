"""Scan observations kept separate from execution and profitability records."""
from collections import Counter

from common.rejection_collector import RejectionLogHandler


SCAN_EVENTS = RejectionLogHandler.CAPTURED_EVENTS | {
    "candidate_evaluated", "scan_with_watchlist", "pool_b_loaded",
    "pool_b_filtered",
    "breakout_rejected", "breakout_skipped", "candidate_skipped",
    "scan_error", "entry_signal_generated", "smart_money_passed", "bgu_matched",
}
NON_REJECTION_EVENTS = {
    "candidate_evaluated", "scan_with_watchlist", "pool_b_loaded",
    "pool_b_filtered",
    "buy_signal_generated", "entry_signal_generated", "smart_money_passed", "bgu_matched",
}


def summarize_scan(date, strategy, events, signals):
    evaluated = {event.code for event in events if event.event == "candidate_evaluated" and event.code}
    signal_codes = {signal.code for signal in signals if signal.action == "BUY"}
    reasons = {(event.code, event.reason) for event in events
               if event.code and event.event not in NON_REJECTION_EVENTS}
    explained = {code for code, _ in reasons} | signal_codes
    watchlist_counts = [event.details.get("count") for event in events
                       if event.event in {"scan_with_watchlist", "pool_b_loaded"}]
    source_counts = [event.details.get("source_counts") for event in events
                     if event.event in {"scan_with_watchlist", "pool_b_loaded"}]
    filtered = [event for event in events if event.event == "pool_b_filtered"]
    return {
        "date": date,
        "strategy": strategy,
        "watchlist_candidates": watchlist_counts[-1] if watchlist_counts else None,
        "candidate_source_counts": source_counts[-1] if source_counts else {},
        "eligible_candidates": filtered[-1].details.get("count") if filtered else None,
        "eligible_candidate_source_counts": (
            filtered[-1].details.get("source_counts") or {} if filtered else {}
        ),
        "evaluated_candidates": len(evaluated),
        "evaluated_codes": sorted(evaluated),
        "buy_signals": sum(signal.action == "BUY" for signal in signals),
        "reason_counts": dict(sorted(Counter(reason for _, reason in reasons).items())),
        "observed_pass_counts": dict(Counter(event.event for event in events
                                             if event.event in {"smart_money_passed", "bgu_matched"})),
        "scan_skip_reasons": dict(Counter(event.reason for event in events if event.event == "scan_skipped")),
        "unexplained_codes": sorted(evaluated - explained),
        "events": [{"event": event.event, "code": event.code, "reason": event.reason}
                   for event in events],
        "limitations": "Counts are unique code/reason per scan; reasons may overlap. Silent filters remain unexplained; pass counts are not inferred.",
    }
