import logging
import json
import pytest

from core.logger import (
    Logger,
    get_strategy_logger,
    get_performance_logger,
    _active_listeners
)
from core.loggers.log_config import reset_log_timestamp_for_test
from core.loggers.json_formatter import JsonFormatter
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler


def test_get_strategy_logger(tmp_path):
    strategy_name = "TestStrategy"
    log_dir = tmp_path / "logs"
    
    reset_log_timestamp_for_test()

    logger = get_strategy_logger(strategy_name, log_dir=str(log_dir))

    assert isinstance(logger, logging.Logger)
    assert logger.name == f"strategy.{strategy_name}"
    assert logger.propagate
    assert len(logger.handlers) == 1

    file_handler = None
    for listener in _active_listeners:
        for h in listener.handlers:
            if isinstance(h, logging.FileHandler):
                file_handler = h
                break
        if file_handler: break
        
    assert file_handler is not None
    assert isinstance(file_handler.formatter, JsonFormatter)

    strategy_log_dir = log_dir / "strategies"
    log_files = list(strategy_log_dir.glob(f"*_{strategy_name}_*.log.json"))
    assert len(log_files) == 1
    log_file_path = log_files[0]
    assert file_handler.baseFilename == str(log_file_path)
    assert file_handler.mode == 'a'

    dict_message = {"event": "test_event", "data": {"code": "005930", "price": 80000}}
    str_message = "This is a simple string message."
    logger.info(dict_message)
    logger.warning(str_message)

    for listener in _active_listeners:
        listener.queue.join()
        for h in listener.handlers:
            h.close()

    assert log_file_path.exists()
    with open(log_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    assert len(lines) == 2

    log1 = json.loads(lines[0])
    assert log1['level'] == 'INFO'
    assert log1['data'] == dict_message

    log2 = json.loads(lines[1])
    assert log2['level'] == 'WARNING'
    assert log2['message'] == str_message
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    reset_log_timestamp_for_test()


def test_get_performance_logger(tmp_path):
    perf_logger = logging.getLogger("performance")
    for handler in perf_logger.handlers[:]:
        handler.close()
        perf_logger.removeHandler(handler)

    log_dir = tmp_path / "logs"
    
    reset_log_timestamp_for_test()

    logger = get_performance_logger(log_dir=str(log_dir))

    assert isinstance(logger, logging.Logger)
    assert logger.name == "performance"
    assert not logger.propagate
    assert len(logger.handlers) == 1

    file_handler = None
    for listener in _active_listeners:
        for h in listener.handlers:
            if isinstance(h, SizeTimeRotatingFileHandler):
                file_handler = h
                break
        if file_handler: break
        
    assert file_handler is not None
    assert not isinstance(file_handler.formatter, JsonFormatter)

    perf_log_dir = log_dir / "performance"
    log_files = list(perf_log_dir.glob("*_perf_*.log"))
    assert len(log_files) == 1
    log_file_path = log_files[0]
    assert file_handler.baseFilename == str(log_file_path)

    msg = "Performance test message"
    logger.info(msg)

    for listener in _active_listeners:
        listener.queue.join()
        for h in listener.handlers:
            h.close()

    assert log_file_path.exists()
    content = log_file_path.read_text(encoding='utf-8')
    assert msg in content
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    reset_log_timestamp_for_test()


def test_loggers_use_custom_handler(tmp_path):
    reset_log_timestamp_for_test()
    log_dir = tmp_path / "logs"

    strat_logger = get_strategy_logger("test_strat", log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for l in _active_listeners for h in l.handlers)
    for listener in _active_listeners:
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()

    app_logger = Logger(log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for l in app_logger._listeners for h in l.handlers)

    app_logger.close()
