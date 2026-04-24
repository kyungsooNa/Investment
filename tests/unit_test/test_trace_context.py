# tests/unit_test/test_trace_context.py
import re
import pytest
from core.loggers.trace_context import (
    _trace_id_var,
    get_trace_id,
    new_trace_id,
    trace_scope,
)


def test_get_trace_id_returns_none_when_empty():
    _trace_id_var.set("")
    assert get_trace_id() is None


def test_trace_scope_sets_and_restores():
    _trace_id_var.set("")
    with trace_scope("abc-123"):
        assert get_trace_id() == "abc-123"
    assert get_trace_id() is None


def test_trace_scope_restores_previous_value():
    token = _trace_id_var.set("outer")
    try:
        with trace_scope("inner"):
            assert get_trace_id() == "inner"
        assert get_trace_id() == "outer"
    finally:
        _trace_id_var.reset(token)


def test_trace_scope_noop_on_empty_string():
    token = _trace_id_var.set("parent")
    try:
        with trace_scope(""):
            assert get_trace_id() == "parent"
        assert get_trace_id() == "parent"
    finally:
        _trace_id_var.reset(token)


def test_trace_scope_restores_on_exception():
    _trace_id_var.set("")
    with pytest.raises(ValueError):
        with trace_scope("err-trace"):
            assert get_trace_id() == "err-trace"
            raise ValueError("oops")
    assert get_trace_id() is None


def test_trace_scope_nested():
    _trace_id_var.set("")
    with trace_scope("outer"):
        assert get_trace_id() == "outer"
        with trace_scope("inner"):
            assert get_trace_id() == "inner"
        assert get_trace_id() == "outer"
    assert get_trace_id() is None


def test_new_trace_id_format():
    tid = new_trace_id("MOMENTUM")
    # 형식: MOMEN-yyyymmddHHMMSS-xxxxxxxx
    pattern = r'^[A-Z_]{1,6}-\d{14}-[0-9a-f]{8}$'
    assert re.match(pattern, tid), f"Unexpected format: {tid}"


def test_new_trace_id_empty_strategy():
    tid = new_trace_id("")
    assert tid.startswith("TRACE-")


def test_new_trace_id_uniqueness():
    ids = {new_trace_id("STRAT") for _ in range(20)}
    assert len(ids) == 20
