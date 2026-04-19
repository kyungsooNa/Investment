import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from scheduler.ticket_queue.dlq_manager import DlqManager, _DEFAULT_DLQ_LOG_PATH
from scheduler.ticket_queue.ticket import Ticket


def _make_ticket(task_name="my_task", priority=1, attempt=3, payload=None):
    t = Ticket(priority=priority, task_name=task_name, payload=payload or {"k": "v"})
    t.attempt = attempt
    return t


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

def test_init_uses_default_logger_and_path():
    mgr = DlqManager()
    assert mgr._logger is not None
    assert mgr._log_path == _DEFAULT_DLQ_LOG_PATH


def test_init_accepts_custom_logger_and_path():
    logger = MagicMock(spec=logging.Logger)
    mgr = DlqManager(logger=logger, log_path="/tmp/custom.jsonl")
    assert mgr._logger is logger
    assert mgr._log_path == "/tmp/custom.jsonl"


# ---------------------------------------------------------------------------
# handle_failed_ticket
# ---------------------------------------------------------------------------

async def test_handle_failed_ticket_logs_critical():
    logger = MagicMock(spec=logging.Logger)
    mgr = DlqManager(logger=logger, log_path="/tmp/dlq_test.jsonl")
    ticket = _make_ticket(task_name="scan_task", attempt=5, payload={"x": 1})

    with patch.object(mgr, "_append_to_log"):
        await mgr.handle_failed_ticket(ticket, "timeout")

    logger.critical.assert_called_once()
    msg = logger.critical.call_args[0][0]
    assert "scan_task" in msg
    assert "5" in msg
    assert "timeout" in msg


async def test_handle_failed_ticket_calls_append_to_log():
    logger = MagicMock(spec=logging.Logger)
    mgr = DlqManager(logger=logger, log_path="/tmp/dlq_test.jsonl")
    ticket = _make_ticket()

    with patch.object(mgr, "_append_to_log") as mock_append:
        await mgr.handle_failed_ticket(ticket, "some error")

    mock_append.assert_called_once_with(ticket, "some error")


# ---------------------------------------------------------------------------
# _append_to_log — happy path
# ---------------------------------------------------------------------------

def test_append_to_log_writes_valid_jsonl(tmp_path):
    log_file = tmp_path / "dlq.jsonl"
    logger = MagicMock(spec=logging.Logger)
    mgr = DlqManager(logger=logger, log_path=str(log_file))
    ticket = _make_ticket(task_name="t1", priority=2, attempt=4, payload={"a": 1})

    mgr._append_to_log(ticket, "boom")

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_name"] == "t1"
    assert entry["priority"] == 2
    assert entry["attempt"] == 4
    assert entry["payload"] == {"a": 1}
    assert entry["error"] == "boom"
    assert "timestamp" in entry
    assert "created_at" in entry


def test_append_to_log_appends_multiple_entries(tmp_path):
    log_file = tmp_path / "dlq.jsonl"
    mgr = DlqManager(log_path=str(log_file))
    t1 = _make_ticket(task_name="A")
    t2 = _make_ticket(task_name="B")

    mgr._append_to_log(t1, "err1")
    mgr._append_to_log(t2, "err2")

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_name"] == "A"
    assert json.loads(lines[1])["task_name"] == "B"


def test_append_to_log_creates_missing_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "dlq.jsonl"
    mgr = DlqManager(log_path=str(nested))
    mgr._append_to_log(_make_ticket(), "err")

    assert nested.exists()


# ---------------------------------------------------------------------------
# _append_to_log — error path
# ---------------------------------------------------------------------------

def test_append_to_log_logs_error_on_write_failure():
    logger = MagicMock(spec=logging.Logger)
    mgr = DlqManager(logger=logger, log_path="/tmp/dlq_err.jsonl")

    with patch("scheduler.ticket_queue.dlq_manager.os.makedirs"), \
         patch("builtins.open", side_effect=OSError("disk full")):
        mgr._append_to_log(_make_ticket(), "err")

    logger.error.assert_called_once()
    assert "disk full" in logger.error.call_args[0][0]
