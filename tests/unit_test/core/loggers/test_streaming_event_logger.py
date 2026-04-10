import os
import json
import logging
import pytest

from core.loggers.streaming_event_logger import StreamingEventLogger
from core.logger import get_streaming_logger
import core.logger
from core.loggers.log_config import reset_log_timestamp_for_test
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.json_formatter import JsonFormatter

@pytest.fixture
def streaming_logger_setup(tmp_path):
    reset_log_timestamp_for_test()

    existing = logging.getLogger("streaming_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    streaming_logger = get_streaming_logger(log_dir=str(log_dir))

    yield streaming_logger, log_dir / "streaming"

    inner = logging.getLogger("streaming_event")
    for h in inner.handlers[:]:
        h.close()
        inner.removeHandler(h)
    
    # 리스너 정리
    for listener in core.logger._active_listeners[:]:
        listener.stop()
    core.logger._active_listeners.clear()


def _read_json_lines(log_dir):
    files = list(log_dir.glob("*.log.json"))
    assert len(files) == 1
    with open(files[0], encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def _flush_streaming_logger():
    for listener in core.logger._active_listeners:
        listener.queue.join()
        for h in listener.handlers:
            h.flush()

def test_get_streaming_logger_creates_file(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    assert isinstance(streaming_logger, StreamingEventLogger)
    assert streaming_log_dir.is_dir()

    inner = logging.getLogger("streaming_event")
    assert not inner.propagate
    assert len(inner.handlers) == 1
    
    handler = None
    for listener in core.logger._active_listeners:
        for h in listener.handlers:
            if isinstance(h, SizeTimeRotatingFileHandler):
                handler = h
                break
        if handler: break
                
    assert isinstance(handler, SizeTimeRotatingFileHandler)
    assert isinstance(handler.formatter, JsonFormatter)

    streaming_logger.log_connect()
    _flush_streaming_logger()

    log_files = list(streaming_log_dir.glob("*_streaming_*.log.json"))
    assert len(log_files) == 1


def test_log_connect_writes_json(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_connect()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 1
    assert lines[0]["data"]["action"] == "connect"
    assert lines[0]["level"] == "INFO"


def test_log_disconnect_writes_reason(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_disconnect(reason="market_closed")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "disconnect"
    assert lines[0]["data"]["reason"] == "market_closed"


def test_log_subscribe_writes_categories_and_count(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_subscribe(
        code="005930",
        categories={"portfolio": 1, "strategy_momentum": 2},
        active_count=3,
    )

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "subscribe"
    assert d["code"] == "005930"
    assert d["categories"] == {"portfolio": 1, "strategy_momentum": 2}
    assert d["active_count"] == 3


def test_log_unsubscribe_writes_code_and_count(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_unsubscribe(code="005930", active_count=2)

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "unsubscribe"
    assert d["code"] == "005930"
    assert d["active_count"] == 2


def test_log_summary_writes_full_state(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_summary(
        active_count=2,
        active_codes=["005930", "000660"],
        pending_by_priority={"HIGH": ["005930"], "MEDIUM": ["000660"], "LOW": []},
    )

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "summary"
    assert d["active_count"] == 2
    assert d["active_codes"] == ["000660", "005930"]  # sorted
    assert d["pending_by_priority"]["HIGH"] == ["005930"]


def test_log_reconnect_writes_trigger_and_stats(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_reconnect(
        trigger="receive_task_dead",
        codes=["005930", "000660"],
        success=2,
        total=2,
    )

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "reconnect"
    assert d["trigger"] == "receive_task_dead"
    assert d["codes"] == ["000660", "005930"]
    assert d["success"] == 2
    assert d["total"] == 2


def test_log_restore_writes_stats(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_restore(codes=["005930"], success=1, total=1)

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "restore"
    assert d["codes"] == ["005930"]
    assert d["success"] == 1
    assert d["total"] == 1


def test_log_pt_subscribe_and_unsubscribe(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_subscribe(code="005930", reason="reconnect")
    streaming_logger.log_pt_unsubscribe(code="005930", reason="reconnect_failed")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2

    assert lines[0]["data"]["action"] == "pt_subscribe"
    assert lines[0]["data"]["code"] == "005930"
    assert lines[0]["data"]["reason"] == "reconnect"

    assert lines[1]["data"]["action"] == "pt_unsubscribe"
    assert lines[1]["data"]["reason"] == "reconnect_failed"


def test_log_price_subscribe_and_unsubscribe(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_price_subscribe(code="000660", reason="restore")
    streaming_logger.log_price_unsubscribe(code="000660", reason="restore_failed")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2

    assert lines[0]["data"]["action"] == "price_subscribe"
    assert lines[0]["data"]["code"] == "000660"
    assert lines[0]["data"]["reason"] == "restore"

    assert lines[1]["data"]["action"] == "price_unsubscribe"
    assert lines[1]["data"]["reason"] == "restore_failed"


def test_get_streaming_logger_returns_same_file_on_second_call(tmp_path):
    reset_log_timestamp_for_test()

    existing = logging.getLogger("streaming_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"

    logger1 = get_streaming_logger(log_dir=str(log_dir))
    logger2 = get_streaming_logger(log_dir=str(log_dir))

    logger1.log_connect()
    _flush_streaming_logger()

    streaming_log_dir = log_dir / "streaming"
    log_files = list(streaming_log_dir.glob("*_streaming_*.log.json"))
    assert len(log_files) == 1

    for h in logging.getLogger("streaming_event").handlers[:]:
        h.close()
        logging.getLogger("streaming_event").removeHandler(h)

def test_log_subscription_policy_extended_events(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_clear_active_state("active clear msg")
    streaming_logger.log_dropped_subscriptions("dropped subscriptions msg")
    streaming_logger.log_add_subscription_rejection(code="005930", message="rejection msg")
    streaming_logger.log_subscribe_pending(code="005930", message="pending msg")
    streaming_logger.log_subscribe_failure(code="005930", message="subscribe error msg")
    streaming_logger.log_unsubscribe_failure(code="005930", message="unsubscribe error msg")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 6

    assert lines[0]["data"]["message"] == "active clear msg"
    assert lines[1]["level"] == "WARNING"
    assert lines[1]["data"]["message"] == "dropped subscriptions msg"
    assert lines[2]["level"] == "WARNING"
    assert lines[2]["data"]["message"] == "rejection msg"
    assert lines[3]["level"] == "INFO"
    assert lines[3]["data"]["message"] == "pending msg"
    assert lines[4]["level"] == "ERROR"
    assert lines[4]["data"]["message"] == "subscribe error msg"
    assert lines[5]["level"] == "ERROR"
    assert lines[5]["data"]["message"] == "unsubscribe error msg"


def test_log_watchdog_start(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_watchdog_start(task_count=2)

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 1
    d = lines[0]["data"]
    assert d["action"] == "watchdog_start"
    assert d["task_count"] == 2
    assert lines[0]["level"] == "INFO"


def test_log_watchdog_stop_lifecycle(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_watchdog_stop_start(task_count=3)
    streaming_logger.log_watchdog_stop_done()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2
    assert lines[0]["data"]["action"] == "watchdog_stop_start"
    assert lines[0]["data"]["task_count"] == 3
    assert lines[1]["data"]["action"] == "watchdog_stop_done"


def test_log_watchdog_suspend_and_resume(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_watchdog_suspend()
    streaming_logger.log_watchdog_resume()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "watchdog_suspend"
    assert lines[0]["level"] == "INFO"
    assert lines[1]["data"]["action"] == "watchdog_resume"
    assert lines[1]["level"] == "INFO"


def test_log_market_closed_disconnect(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_market_closed_disconnect()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "market_closed_disconnect"
    assert lines[0]["level"] == "INFO"


def test_log_market_open_connect(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_market_open_connect()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "market_open_connect"
    assert lines[0]["level"] == "INFO"


def test_log_receive_task_dead(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_receive_task_dead()

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "receive_task_dead"
    assert lines[0]["level"] == "WARNING"


def test_log_pt_data_gap(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_data_gap(data_gap_sec=310.7, threshold_sec=300)

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "pt_data_gap"
    assert d["data_gap_sec"] == 310.7
    assert d["threshold_sec"] == 300
    assert lines[0]["level"] == "WARNING"


def test_log_watchdog_error(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_watchdog_error("connection reset by peer")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "watchdog_error"
    assert d["message"] == "connection reset by peer"
    assert lines[0]["level"] == "ERROR"


def test_log_pt_restore_connect_failed(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_restore_connect_failed("005930")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "pt_restore_connect_failed"
    assert d["code"] == "005930"
    assert lines[0]["level"] == "WARNING"


def test_log_pt_restore_error(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_restore_error("000660", "timeout")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "pt_restore_error"
    assert d["code"] == "000660"
    assert d["error"] == "timeout"
    assert lines[0]["level"] == "ERROR"


def test_log_pt_restore_failed_removed(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_restore_failed_removed(["000660", "005930"])

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "pt_restore_failed_removed"
    assert d["codes"] == ["000660", "005930"]
    assert lines[0]["level"] == "WARNING"


def test_log_price_restore_start_and_done(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_price_restore_start(desired_count=5)
    streaming_logger.log_price_restore_done(active_count=4)

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2
    assert lines[0]["data"]["action"] == "price_restore_start"
    assert lines[0]["data"]["desired_count"] == 5
    assert lines[1]["data"]["action"] == "price_restore_done"
    assert lines[1]["data"]["active_count"] == 4


def test_log_force_reconnect_start(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_force_reconnect_start(
        trigger="receive_task_dead",
        pt_codes=["000660", "005930"],
    )

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "force_reconnect_start"
    assert d["trigger"] == "receive_task_dead"
    assert d["pt_codes"] == ["000660", "005930"]
    assert lines[0]["level"] == "INFO"


def test_log_force_reconnect_disconnect_error(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_force_reconnect_disconnect_error("Disconnect Error")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "force_reconnect_disconnect_error"
    assert d["error"] == "Disconnect Error"
    assert lines[0]["level"] == "WARNING"


def test_log_force_reconnect_done(streaming_logger_setup):
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_force_reconnect_done("manual")

    _flush_streaming_logger()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "force_reconnect_done"
    assert d["trigger"] == "manual"
    assert lines[0]["level"] == "INFO"
