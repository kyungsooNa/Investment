import logging
import pytest
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.app_logger import Logger
from core.loggers.log_config import reset_log_timestamp_for_test

@pytest.fixture
def clean_logger_instance(tmp_path):
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


def test_log_rotation(clean_logger_instance):
    """
    RotatingFileHandler가 설정된 크기를 초과하면 인덱싱 방식(_1, _2...)으로 로그 파일을 회전시키는지 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    
    # 리스너에서 SizeTimeRotatingFileHandler 추출
    handler = None
    for listener in logger._listeners:
        for h in listener.handlers:
            if isinstance(h, SizeTimeRotatingFileHandler):
                handler = h
                break
        if handler:
            break
    
    handler.maxBytes = 200

    msg = "A" * 50
    logger.info(msg)
    logger.flush()
    
    log_files = list(common_log_dir.glob("*_operational_*.log"))
    assert len(log_files) == 1
    
    logger.info(msg)
    logger.flush()
    
    log_files = list(common_log_dir.glob("*_operational_*.log"))
    assert len(log_files) >= 1
    
    assert any(f.name.endswith("_1.log") for f in log_files)


def test_size_time_rotating_handler_backup_limit(tmp_path):
    """
    SizeTimeRotatingFileHandler가 backupCount 제한을 넘어가면 오래된 파일(낮은 인덱스)을 삭제하는지 검증합니다.
    """
    log_file = tmp_path / "test_backup.log"
    backup_count = 2
    
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10, backupCount=backup_count)
    logger = logging.getLogger("test_backup_limit_logger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    
    for i in range(4):
        logger.info(f"Log message {i} " * 5)

    handler.close()
    logger.removeHandler(handler)

    files = list(tmp_path.glob("test_backup_*.log"))
    assert len(files) == backup_count + 1

    filenames = [f.name for f in files]
    assert "test_backup_3.log" in filenames
    assert "test_backup_4.log" in filenames
    assert "test_backup_5.log" in filenames
    assert "test_backup_1.log" not in filenames
    assert "test_backup_2.log" not in filenames
