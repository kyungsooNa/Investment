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
        listener.stop()
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()

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
        listener.stop()
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()

    assert log_file_path.exists()
    content = log_file_path.read_text(encoding='utf-8')
    assert msg in content
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    reset_log_timestamp_for_test()


def test_get_strategy_logger_is_idempotent_within_process(tmp_path):
    """같은 (strategy_name, log_dir) 로 반복 호출해도 신규 _N 파일이 누적되지 않는다.

    회귀: 이전 구현은 호출마다 logger.handlers 를 리셋하고 SizeTimeRotatingFileHandler 를
    재생성하면서 _find_max_index() + 1 로 새 인덱스 파일을 만들어 동일 프로세스 내에서도
    호출 횟수 = 파일 개수로 누적되었다.
    """
    strategy_name = "IdempotencyStrategy"
    log_dir = tmp_path / "logs"

    reset_log_timestamp_for_test()

    logger_a = get_strategy_logger(strategy_name, log_dir=str(log_dir))
    logger_b = get_strategy_logger(strategy_name, log_dir=str(log_dir))
    logger_c = get_strategy_logger(strategy_name, log_dir=str(log_dir))

    assert logger_a is logger_b is logger_c
    assert len(logger_a.handlers) == 1

    strategy_log_dir = log_dir / "strategies"
    log_files = list(strategy_log_dir.glob(f"*_{strategy_name}_*.log.json"))
    assert len(log_files) == 1, (
        f"동일 프로세스 내 반복 호출 시 단일 파일에만 append 되어야 하는데 누적됨: {log_files}"
    )

    for listener in _active_listeners:
        listener.stop()
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()
    for handler in logger_a.handlers[:]:
        logger_a.removeHandler(handler)
    if hasattr(logger_a, "_strategy_log_dir"):
        delattr(logger_a, "_strategy_log_dir")
    reset_log_timestamp_for_test()


def test_get_strategy_logger_reconfigures_when_log_dir_changes(tmp_path):
    """log_dir 이 바뀌면 (테스트 tmp_path 격리 등) 기존 핸들러를 정리하고 재구성한다."""
    strategy_name = "RelocateStrategy"
    log_dir_a = tmp_path / "logs_a"
    log_dir_b = tmp_path / "logs_b"

    reset_log_timestamp_for_test()

    logger = get_strategy_logger(strategy_name, log_dir=str(log_dir_a))
    assert (log_dir_a / "strategies").exists()

    logger2 = get_strategy_logger(strategy_name, log_dir=str(log_dir_b))
    assert logger is logger2  # 동일 logger 인스턴스 (logging.getLogger 의 전역성)
    assert (log_dir_b / "strategies").exists()

    files_b = list((log_dir_b / "strategies").glob(f"*_{strategy_name}_*.log.json"))
    assert len(files_b) == 1

    for listener in _active_listeners:
        listener.stop()
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    if hasattr(logger, "_strategy_log_dir"):
        delattr(logger, "_strategy_log_dir")
    reset_log_timestamp_for_test()


def test_loggers_use_custom_handler(tmp_path):
    reset_log_timestamp_for_test()
    log_dir = tmp_path / "logs"

    strat_logger = get_strategy_logger("test_strat", log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for l in _active_listeners for h in l.handlers)
    for listener in _active_listeners:
        listener.stop()
        for h in listener.handlers:
            h.close()
    _active_listeners.clear()

    app_logger = Logger(log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for l in app_logger._listeners for h in l.handlers)

    app_logger.close()
