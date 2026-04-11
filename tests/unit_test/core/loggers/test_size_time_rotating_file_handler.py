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


def test_json_extension_handling(tmp_path):
    """
    .log.json 확장자를 가진 파일에 대해 제대로 인덱싱이 되는지 검증합니다.
    """
    log_file = tmp_path / "test_json.log.json"
    
    # 기존 파일이 있을 때 _find_max_index 검증
    (tmp_path / "test_json_3.log.json").touch()
    (tmp_path / "test_json_4.log.json").touch()
    
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10)
    # 인덱스 4 다음인 5로 시작하는지 확인
    assert handler.baseFilename.endswith("test_json_5.log.json")
    handler.close()


def test_find_max_index_with_empty_suffix(tmp_path):
    """
    glob 패턴에는 맞지만 인덱스가 비어있는 경우(_ .log)를 무시하는지 테스트합니다.
    """
    log_file = tmp_path / "test_invalid.log"
    (tmp_path / "test_invalid_.log").touch()     # isdigit() == False
    (tmp_path / "test_invalid_2.log").touch()
    
    handler = SizeTimeRotatingFileHandler(str(log_file))
    assert handler.baseFilename.endswith("test_invalid_3.log")
    handler.close()


def test_do_rollover_oserror_suppression(tmp_path, monkeypatch):
    """
    오래된 파일을 삭제할 때 OSError가 발생하더라도 로그 로테이션이 중단되지 않아야 함을 테스트합니다.
    """
    import os
    log_file = tmp_path / "test_oserror.log"
    (tmp_path / "test_oserror_1.log").touch()
    (tmp_path / "test_oserror_2.log").touch()
    (tmp_path / "test_oserror_3.log").touch()
    
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10, backupCount=1)
    
    original_remove = os.remove
    def mock_remove(path):
        if str(path).endswith("test_oserror_1.log"):
            raise OSError("Mock Permission Denied")
        original_remove(path)
        
    monkeypatch.setattr(os, "remove", mock_remove)
    
    try:
        handler.doRollover()
    except OSError:
        pytest.fail("doRollover should suppress OSError during file removal")
        
    handler.close()


def test_rollover_with_delay(tmp_path):
    """
    delay=True로 설정된 경우 doRollover 시 stream을 즉시 열지 않는지 검증합니다.
    """
    log_file = tmp_path / "test_delay.log"
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10, delay=True)
    
    assert handler.stream is None
    handler.doRollover()
    assert handler.stream is None
    
    handler.close()


def test_sort_backup_files_with_invalid_suffix(tmp_path):
    """
    backup 파일 정리 중 _ 뒤의 인덱스가 숫자가 아닐 경우(-1 처리) 정렬 및 삭제가 동작하는지 확인합니다.
    """
    log_file = tmp_path / "test_sort.log"
    (tmp_path / "test_sort_1.log").touch()
    (tmp_path / "test_sort_2.log").touch()
    (tmp_path / "test_sort_.log").touch()  # isdigit() == False -> -1 반환
    
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10, backupCount=1)
    handler.doRollover()
    handler.close()
    
    # backupCount=1이면 총 파일 개수에서 가장 오래된 것들 순서대로 지워짐.
    # 정렬: ["test_sort_.log" (-1), "test_sort_1.log" (1), "test_sort_2.log" (2)]
    # len=3, backupCount=1 -> 앞의 2개 삭제 (test_sort_.log, test_sort_1.log)
    assert not (tmp_path / "test_sort_.log").exists()
    assert not (tmp_path / "test_sort_1.log").exists()
    assert (tmp_path / "test_sort_2.log").exists()
