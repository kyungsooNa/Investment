# tests/unit_test/test_json_formatter.py
import json
import logging
import pytest
from core.loggers.json_formatter import JsonFormatter
from core.loggers.trace_context import _trace_id_var, trace_scope


def _format(msg, extra=None):
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO,
        pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return json.loads(formatter.format(record))


def test_no_trace_id_when_context_empty():
    _trace_id_var.set("")
    obj = _format("hello")
    assert "trace_id" not in obj


def test_trace_id_from_contextvar():
    with trace_scope("CTX-001"):
        obj = _format("hello")
    assert obj["trace_id"] == "CTX-001"


def test_trace_id_from_extra_overrides_contextvar():
    with trace_scope("CTX-002"):
        obj = _format("hello", extra={"trace_id": "EXTRA-999"})
    assert obj["trace_id"] == "EXTRA-999"


def test_message_field_for_string():
    _trace_id_var.set("")
    obj = _format("plain string")
    assert obj["message"] == "plain string"
    assert "data" not in obj


def test_data_field_for_dict():
    _trace_id_var.set("")
    obj = _format({"key": "value"})
    assert obj["data"] == {"key": "value"}
    assert "message" not in obj


def test_standard_fields_present():
    _trace_id_var.set("")
    obj = _format("test")
    assert "timestamp" in obj
    assert "level" in obj
    assert "name" in obj
