import os
import time
import logging
import pytest

from core.loggers.app_logger import Logger
from core.loggers.log_config import reset_log_timestamp_for_test

@pytest.fixture
def clean_logger_instance(tmp_path):
    """
    각 테스트마다 독립적인 로거 인스턴스와 로그 디렉토리를 제공하는 픽스처.
    기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지합니다.
    """
    log_dir = tmp_path / "logs"
    reset_log_timestamp_for_test()
    
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logger_instance = Logger(log_dir=str(log_dir))

    yield logger_instance, log_dir.joinpath("common")

    logger_instance.close()
    for handler in logging.getLogger('operational_logger').handlers:
        handler.close()
        logging.getLogger('operational_logger').removeHandler(handler)
    for handler in logging.getLogger('debug_logger').handlers:
        handler.close()
        logging.getLogger('debug_logger').removeHandler(handler)

    logging.root.handlers = original_handlers

def test_logger_creates_log_files(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance

    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")
    logger.flush()

    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    assert any("debug" in f.name for f in log_files)
    assert any("operational" in f.name for f in log_files)

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert "info message" in content
        assert "warning message" in content
        assert "error message" in content
        assert "critical message" in content
        if "debug" in f.name:
            assert "debug message" in content
            assert "error message" in content
            assert "critical message" in content
        else:
            assert "debug message" not in content

def test_logger_error_with_exc_info(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance
    message = "Test error with exception"
    try:
        raise ValueError("Something went wrong")
    except ValueError as e:
        logger.error(message, exc_info=True)
    logger.flush()

    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "ValueError: Something went wrong" in content
        assert "Traceback" in content

def test_logger_critical_with_exc_info(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance
    message = "Test critical with exception"
    try:
        raise RuntimeError("Critical error occurred")
    except RuntimeError as e:
        logger.critical(message, exc_info=True)
    logger.flush()

    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "RuntimeError: Critical error occurred" in content
        assert "Traceback" in content


def test_logger_critical_accepts_logging_format_args(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance

    logger.critical("[KillSwitch] 트립! 사유: %s | 메타: %s", "연속 API 오류", {"last_reason": "HTTP 500"})
    logger.flush()

    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert "[KillSwitch] 트립! 사유: 연속 API 오류 | 메타: {'last_reason': 'HTTP 500'}" in content


def test_logger_creates_log_dir_if_not_exists(tmp_path):
    non_existent_log_dir = tmp_path / "non_existent_logs_dir"

    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    reset_log_timestamp_for_test()
    logger = Logger(log_dir=str(non_existent_log_dir))

    assert non_existent_log_dir.is_dir()
    assert (non_existent_log_dir / "common").is_dir()
    assert (non_existent_log_dir / "strategies").is_dir()

    logger.close()
    for handler in logging.getLogger('operational_logger').handlers:
        handler.close()
        logging.getLogger('operational_logger').removeHandler(handler)
    for handler in logging.getLogger('debug_logger').handlers:
        handler.close()
        logging.getLogger('debug_logger').removeHandler(handler)

    if os.path.exists(non_existent_log_dir):
        import shutil
        shutil.rmtree(non_existent_log_dir)

    logging.root.handlers = original_handlers


def test_logger_reinitializes_after_previous_close(tmp_path):
    """이전 Logger.close() 후 남은 QueueHandler가 새 로그 파일 생성을 막지 않아야 한다."""
    reset_log_timestamp_for_test()

    log_dir_a = tmp_path / "logs_a"
    first = Logger(log_dir=str(log_dir_a))
    first.close()

    log_dir_b = tmp_path / "logs_b"
    second = Logger(log_dir=str(log_dir_b))
    try:
        second.error("second logger writes")
        second.flush()

        log_files = list((log_dir_b / "common").glob("*.log"))
        assert len(log_files) == 2
        assert all("second logger writes" in f.read_text(encoding="utf-8") for f in log_files)
    finally:
        second.close()
        for name in ["operational_logger", "debug_logger"]:
            inner = logging.getLogger(name)
            for handler in inner.handlers[:]:
                handler.close()
                inner.removeHandler(handler)


def test_log_cleanup(tmp_path):
    log_dir = tmp_path / "logs_cleanup_test"
    common_dir = log_dir / "common"
    strategies_dir = log_dir / "strategies"
    os.makedirs(common_dir)
    os.makedirs(strategies_dir)
    
    old_log = common_dir / "old_app.log"
    old_log.touch()
    old_json = strategies_dir / "old_strat.log.json"
    old_json.touch()
    
    days_ago_31 = time.time() - (31 * 86400)
    os.utime(old_log, (days_ago_31, days_ago_31))
    os.utime(old_json, (days_ago_31, days_ago_31))
    
    recent_log = common_dir / "recent_app.log"
    recent_log.touch()
    
    days_ago_1 = time.time() - (1 * 86400)
    os.utime(recent_log, (days_ago_1, days_ago_1))
    
    other_file = common_dir / "readme.txt"
    other_file.touch()
    os.utime(other_file, (days_ago_31, days_ago_31))

    reset_log_timestamp_for_test()
    original_handlers = logging.root.handlers[:]
    logging.root.handlers = []
    
    try:
        logger = Logger(log_dir=str(log_dir))
        logger.close()
        
        assert not old_log.exists(), "30일 지난 .log 파일은 삭제되어야 합니다."
        assert not old_json.exists(), "30일 지난 .json 파일은 삭제되어야 합니다."
        assert recent_log.exists(), "최신 로그 파일은 유지되어야 합니다."
        assert other_file.exists(), "로그 확장자가 아닌 파일은 유지되어야 합니다."
        
    finally:
        for name in ['operational_logger', 'debug_logger']:
            logger = logging.getLogger(name)
            for h in logger.handlers[:]:
                h.close()
                logger.removeHandler(h)
        
        logging.root.handlers = original_handlers

def test_logger_exception_method(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance
    message = "Test exception method"
    try:
        raise ValueError("Exception method test")
    except ValueError:
        logger.exception(message)
    logger.flush()

    log_files = list(common_log_dir.glob("*.log"))
    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "ValueError: Exception method test" in content
        assert "Traceback" in content
