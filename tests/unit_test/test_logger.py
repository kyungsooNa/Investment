import os
import time
import logging
import json
from unittest.mock import patch, MagicMock
from logging.handlers import RotatingFileHandler

import pytest

# 실제 core.logger 경로에 맞게 수정
from core.logger import Logger, get_strategy_logger, JsonFormatter, reset_log_timestamp_for_test


@pytest.fixture
def clean_logger_instance(tmp_path):
    """
    각 테스트마다 독립적인 로거 인스턴스와 로그 디렉토리를 제공하는 픽스처.
    기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지합니다.
    """
    # 테스트 로거 디렉토리 설정
    log_dir = tmp_path / "logs"
    # Logger 클래스가 common 디렉토리를 생성하므로 미리 만들 필요 없음

    # 전역 타임스탬프 리셋
    reset_log_timestamp_for_test()
    
    # 기존 root 로거 핸들러 백업 및 제거
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 로거 인스턴스 생성
    logger_instance = Logger(log_dir=str(log_dir))

    yield logger_instance, log_dir.joinpath("common") # 검증을 위해 common 디렉토리 경로 반환

    # 테스트 후 정리
    # 로거 핸들러 닫기 (파일 잠금 해제)
    for handler in logging.getLogger('operational_logger').handlers:
        handler.close()
        logging.getLogger('operational_logger').removeHandler(handler)
    for handler in logging.getLogger('debug_logger').handlers:
        handler.close()
        logging.getLogger('debug_logger').removeHandler(handler)

    # 기존 root 로거 핸들러 복원
    logging.root.handlers = original_handlers


def test_logger_creates_log_files(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance

    # When
    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")

    # Then
    # `logs/common` 디렉토리에서 로그 파일 검색
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2  # operational, debug

    # 파일 이름 형식 확인
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
            # 수정된 부분: _get_caller_info가 반환할 수 있는 두 가지 경우를 모두 허용
            # 1. 'unknown - message' (inspect.currentframe이 None을 반환하는 등 호출자를 찾지 못할 때)
            # 2. 'filename:lineno - message' (호출자를 찾을 때)
            # test_logger_creates_log_files 자체의 파일 이름과, pytest 실행 스크립트의 이름을 모두 고려합니다.
            assert ("unknown - error message" in content or
                    f"{os.path.basename(__file__).replace('.py', '')}:" in content and "error message" in content or
                    "python.py:" in content and "error message" in content) #
            assert ("unknown - critical message" in content or
                    f"{os.path.basename(__file__).replace('.py', '')}:" in content and "critical message" in content or
                    "python.py:" in content and "critical message" in content) #
        else:
            assert "debug message" not in content


def test_logger_error_with_exc_info(clean_logger_instance):
    """
    error() 메서드가 exc_info=True로 호출될 때의 로깅을 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    message = "Test error with exception"
    try:
        raise ValueError("Something went wrong")
    except ValueError as e:
        logger.error(message, exc_info=True)

    # Then
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "ValueError: Something went wrong" in content # 예외 정보 포함 확인
        assert "Traceback" in content # 스택 트레이스 포함 확인


def test_logger_critical_with_exc_info(clean_logger_instance):
    """
    critical() 메서드가 exc_info=True로 호출될 때의 로깅을 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    message = "Test critical with exception"
    try:
        raise RuntimeError("Critical error occurred")
    except RuntimeError as e:
        logger.critical(message, exc_info=True)

    # Then
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "RuntimeError: Critical error occurred" in content
        assert "Traceback" in content


@patch('inspect.currentframe')
def test_logger_get_caller_info_unknown_caller(mock_currentframe, clean_logger_instance):
    """
    _get_caller_info가 호출자 정보를 찾지 못해 'unknown'을 반환하는 경우를 테스트합니다.
    이것이 `logger.py`의 94번 라인을 커버합니다.
    """
    logger, _ = clean_logger_instance

    # inspect.currentframe()이 첫 번째 호출에서 스택의 끝 (None)을 반환하도록 모킹합니다.
    # 이렇게 하면 _get_caller_info 내의 `while frame:` 루프가 즉시 종료되고
    # `return "unknown"` (94번 라인)이 실행됩니다.
    mock_currentframe.return_value = None

    # _get_caller_info 메서드 호출
    caller_info = logger._get_caller_info()

    # "unknown"이 반환되었는지 검증
    assert caller_info == "unknown"
    mock_currentframe.assert_called_once() # currentframe이 한 번 호출되었는지 확인


@patch('inspect.currentframe')
@patch('inspect.getframeinfo')
def test_logger_get_caller_info_skips_logger_frames(mock_getframeinfo, mock_currentframe, clean_logger_instance):
    """
    _get_caller_info가 logger.py 내부의 프레임을 건너뛰고 외부 호출자를 올바르게 찾는 경우를 테스트합니다.
    이는 87-94 라인에 걸친 _get_caller_info의 핵심 로직을 커버합니다.
    """
    logger, _ = clean_logger_instance

    # Mock 프레임 객체 생성
    # inspect.currentframe()의 return_value로 사용할 프레임 체인 설정:
    # `outer_caller_frame`은 `f_back=None`으로 스택의 끝을 나타냅니다.
    # `inner_logger_frame`은 `f_back`으로 `outer_caller_frame`을 가리킵니다.
    # `mock_currentframe.return_value`는 이 체인의 시작인 `inner_logger_frame`이 됩니다.
    mock_outer_caller_frame = MagicMock(f_back=None) # 외부 호출자 프레임
    mock_inner_logger_frame = MagicMock(f_back=mock_outer_caller_frame) # 로거 내부 프레임

    # inspect.currentframe()은 한 번만 호출되므로 return_value에 체인의 시작을 설정합니다.
    mock_currentframe.return_value = mock_inner_logger_frame

    # inspect.getframeinfo가 각 프레임에 대해 반환할 정보 설정
    # _get_caller_info 함수 내의 `while` 루프에서 `inspect.getframeinfo(frame)`가 두 번 호출됩니다.
    # 첫 번째는 `inner_logger_frame`에 대한 정보, 두 번째는 `outer_caller_frame`에 대한 정보입니다.
    mock_getframeinfo.side_effect = [
        MagicMock(filename="path/to/core/logger.py", lineno=73), # logger.py 내부의 프레임 정보
        MagicMock(filename="path/to/my_app/main_script.py", lineno=100) # 외부 호출자 프레임 정보
    ]

    # _get_caller_info 메서드 호출
    caller_info = logger._get_caller_info()

    # 검증
    assert caller_info == "main_script.py:100"
    # inspect.currentframe()은 함수 시작 시 한 번만 호출됩니다.
    assert mock_currentframe.call_count == 1
    # inspect.getframeinfo()는 `while` 루프에서 두 번 호출됩니다.
    assert mock_getframeinfo.call_count == 2

def test_logger_creates_log_dir_if_not_exists(tmp_path):
    """
    TC: 로그 디렉토리가 존재하지 않을 때 Logger 초기화 시 디렉토리가 생성되는지 테스트합니다.
    이는 core/logger.py의 23-24 라인을 커버합니다.
    """
    # given: tmp_path는 각 테스트마다 비어 있는 임시 디렉토리를 제공합니다.
    # 따라서, tmp_path/non_existent_logs_dir은 처음에는 존재하지 않습니다.
    non_existent_log_dir = tmp_path / "non_existent_logs_dir"

    # when: Logger 인스턴스를 생성할 때, 해당 디렉토리가 생성되어야 합니다.
    # 기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    reset_log_timestamp_for_test()
    logger = Logger(log_dir=str(non_existent_log_dir))

    # then: 로그 디렉토리가 성공적으로 생성되었는지 확인
    assert non_existent_log_dir.is_dir() # 디렉토리가 존재하는지 확인
    assert (non_existent_log_dir / "common").is_dir()
    assert (non_existent_log_dir / "strategies").is_dir()

    # cleanup: 테스트 후 로거 핸들러 및 디렉토리 정리
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


def test_get_strategy_logger(tmp_path):
    """
    get_strategy_logger가 타임스탬프가 포함된 JSON 파일 핸들러를 올바르게 생성하는지 테스트합니다.
    """
    strategy_name = "TestStrategy"
    log_dir = tmp_path / "logs"
    
    # 테스트 격리를 위해 타임스탬프 리셋
    reset_log_timestamp_for_test()

    # 로거 생성
    logger = get_strategy_logger(strategy_name, log_dir=str(log_dir))

    # 1. 로거 속성 검증
    assert isinstance(logger, logging.Logger)
    assert logger.name == f"strategy.{strategy_name}"
    assert not logger.propagate
    assert len(logger.handlers) == 1  # JSON 파일 핸들러만 존재 (콘솔 핸들러 제거됨)

    # 2. 파일 핸들러 검증
    file_handler = next((h for h in logger.handlers if isinstance(h, logging.FileHandler)), None)
    assert file_handler is not None
    assert isinstance(file_handler.formatter, JsonFormatter)

    # 파일명에 타임스탬프가 포함되므로 glob으로 검색
    strategy_log_dir = log_dir / "strategies"
    log_files = list(strategy_log_dir.glob(f"*_{strategy_name}.log.json"))
    assert len(log_files) == 1
    log_file_path = log_files[0]
    assert file_handler.baseFilename == str(log_file_path)
    assert file_handler.mode == 'a'

    # 3. 로깅 동작 검증
    dict_message = {"event": "test_event", "data": {"code": "005930", "price": 80000}}
    str_message = "This is a simple string message."
    logger.info(dict_message)
    logger.warning(str_message)

    # 핸들러를 닫아 파일에 내용이 기록되도록 함
    for handler in logger.handlers[:]:
        handler.close()

    # 5. 로그 파일 내용 검증
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
    
    # 6. 핸들러 정리 (테스트 격리)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 전역 타임스탬프 다시 리셋
    reset_log_timestamp_for_test()


def test_log_rotation(clean_logger_instance):
    """
    RotatingFileHandler가 설정된 크기를 초과하면 로그 파일을 회전시키는지 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    
    # operational_logger의 RotatingFileHandler 찾기
    handler = next(h for h in logger.operational_logger.handlers if isinstance(h, RotatingFileHandler))
    
    # 테스트를 위해 maxBytes를 매우 작게 설정 (예: 100 바이트)
    # 기본 포맷터 헤더 등을 고려하여 100바이트로 설정
    handler.maxBytes = 100
    
    # 1. 첫 번째 로그 기록 (약 50바이트 메시지 + 헤더 -> 80~90바이트 예상)
    msg = "A" * 50
    logger.info(msg)
    
    # 파일이 하나만 있어야 함
    log_files = list(common_log_dir.glob("*_operational.log*"))
    assert len(log_files) == 1
    
    # 2. 두 번째 로그 기록 (누적 100바이트 초과 -> 회전 발생 예상)
    logger.info(msg)
    
    # 백업 파일(.log.1)이 생성되었는지 확인
    log_files = list(common_log_dir.glob("*_operational.log*"))
    # 원본 파일 + 백업 파일(.1)
    assert len(log_files) >= 2
    assert any(f.name.endswith(".1") for f in log_files)


def test_log_cleanup(tmp_path):
    """
    Logger 초기화 시 오래된 로그 파일(30일 이상)이 삭제되는지 테스트합니다.
    """
    # 1. 테스트용 로그 디렉토리 준비
    log_dir = tmp_path / "logs_cleanup_test"
    common_dir = log_dir / "common"
    strategies_dir = log_dir / "strategies"
    os.makedirs(common_dir)
    os.makedirs(strategies_dir)
    
    # 2. 파일 생성
    # A. 삭제되어야 할 오래된 파일 (31일 전)
    old_log = common_dir / "old_app.log"
    old_log.touch()
    old_json = strategies_dir / "old_strat.log.json"
    old_json.touch()
    
    days_ago_31 = time.time() - (31 * 86400)
    os.utime(old_log, (days_ago_31, days_ago_31))
    os.utime(old_json, (days_ago_31, days_ago_31))
    
    # B. 유지되어야 할 최신 파일 (1일 전)
    recent_log = common_dir / "recent_app.log"
    recent_log.touch()
    
    days_ago_1 = time.time() - (1 * 86400)
    os.utime(recent_log, (days_ago_1, days_ago_1))
    
    # C. 로그 파일이 아닌 파일 (삭제되지 않아야 함)
    other_file = common_dir / "readme.txt"
    other_file.touch()
    os.utime(other_file, (days_ago_31, days_ago_31))

    # 3. Logger 초기화 (이때 _cleanup_old_logs가 실행됨)
    # 기존 핸들러 정리 (안전장치)
    reset_log_timestamp_for_test()
    original_handlers = logging.root.handlers[:]
    logging.root.handlers = []
    
    try:
        Logger(log_dir=str(log_dir))
        
        # 4. 검증
        assert not old_log.exists(), "30일 지난 .log 파일은 삭제되어야 합니다."
        assert not old_json.exists(), "30일 지난 .json 파일은 삭제되어야 합니다."
        assert recent_log.exists(), "최신 로그 파일은 유지되어야 합니다."
        assert other_file.exists(), "로그 확장자가 아닌 파일은 유지되어야 합니다."
        
    finally:
        # 정리: 생성된 로거의 핸들러를 닫아야 파일 잠금이 해제됨
        for name in ['operational_logger', 'debug_logger']:
            logger = logging.getLogger(name)
            for h in logger.handlers[:]:
                h.close()
                logger.removeHandler(h)
        
        logging.root.handlers = original_handlers
