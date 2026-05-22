"""EntryRejectionCounter 단위 테스트 (P2 2-2 scan cycle 1차)."""
from __future__ import annotations

import logging

from core.scan_rejection_counter import EntryRejectionCounter


def _record(msg) -> logging.LogRecord:
    return logging.LogRecord(
        name="strategy.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_emit_counts_entry_rejected_by_reason():
    counter = EntryRejectionCounter()
    counter.emit(_record({"event": "entry_rejected", "code": "005930", "reason": "foo"}))
    counter.emit(_record({"event": "entry_rejected", "code": "035720", "reason": "foo"}))
    counter.emit(_record({"event": "entry_rejected", "code": "000660", "reason": "bar"}))

    assert counter.snapshot() == {"foo": 2, "bar": 1}


def test_emit_ignores_non_entry_rejected_events():
    counter = EntryRejectionCounter()
    counter.emit(_record({"event": "scan_started", "reason": "foo"}))
    counter.emit(_record({"event": "other_event", "reason": "bar"}))

    assert counter.snapshot() == {}


def test_emit_ignores_records_with_non_dict_msg():
    counter = EntryRejectionCounter()
    counter.emit(_record("simple string log"))
    counter.emit(_record(["list", "log"]))

    assert counter.snapshot() == {}


def test_emit_ignores_missing_or_empty_reason():
    counter = EntryRejectionCounter()
    counter.emit(_record({"event": "entry_rejected"}))  # reason 없음
    counter.emit(_record({"event": "entry_rejected", "reason": ""}))
    counter.emit(_record({"event": "entry_rejected", "reason": None}))

    assert counter.snapshot() == {}


def test_snapshot_returns_copy():
    counter = EntryRejectionCounter()
    counter.emit(_record({"event": "entry_rejected", "reason": "foo"}))

    snap = counter.snapshot()
    snap["foo"] = 100
    snap["bar"] = 5

    assert counter.snapshot() == {"foo": 1}


def test_reset_clears_counts():
    counter = EntryRejectionCounter()
    counter.emit(_record({"event": "entry_rejected", "reason": "foo"}))
    counter.emit(_record({"event": "entry_rejected", "reason": "bar"}))

    counter.reset()

    assert counter.snapshot() == {}


def test_record_helper_increments_count():
    counter = EntryRejectionCounter()
    counter.record("manual_reason")
    counter.record("manual_reason")

    assert counter.snapshot() == {"manual_reason": 2}


def test_record_helper_ignores_empty_reason():
    counter = EntryRejectionCounter()
    counter.record("")

    assert counter.snapshot() == {}
