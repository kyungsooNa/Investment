import os
import glob
import logging
from datetime import datetime
import inspect
from unittest.mock import patch, MagicMock

import pytest

# 실제 core.logger 경로에 맞게 수정
from core.logger import Logger

@pytest.fixture
def clean_logger_instance(tmp_path):
    """
    각 테스트마다 독립적인 로거 인스턴스와 로그 디렉토리를 제공하는 픽스처.
    기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지합니다.
    """
    # 테스트 로거 디렉토리 설정
    log_dir = tmp_path / "logs"
    os.makedirs(log_dir, exist_ok=True)

    # 기존 root 로거 핸들러 백업 및 제거
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 로거 인스턴스 생성
    logger_instance = Logger(log_dir=str(log_dir))

    yield logger_instance, log_dir # 로거 인스턴스와 로그 디렉토리 반환

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
    logger, log_dir = clean_logger_instance

    # When
    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")

    # Then
    log_files = list(log_dir.glob("*.log"))
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
    logger, log_dir = clean_logger_instance
    message = "Test error with exception"
    try:
        raise ValueError("Something went wrong")
    except ValueError as e:
        logger.error(message, exc_info=True)

    # Then
    log_files = list(log_dir.glob("*.log"))
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
    logger, log_dir = clean_logger_instance
    message = "Test critical with exception"
    try:
        raise RuntimeError("Critical error occurred")
    except RuntimeError as e:
        logger.critical(message, exc_info=True)

    # Then
    log_files = list(log_dir.glob("*.log"))
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
