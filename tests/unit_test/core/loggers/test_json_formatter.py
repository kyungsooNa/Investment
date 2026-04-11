import json
import logging
import sys
import pytest
from core.loggers.json_formatter import JsonFormatter


@pytest.fixture
def json_formatter():
    """JsonFormatter 인스턴스를 제공하는 픽스처"""
    # 시간 형식을 고정하여 테스트를 용이하게 할 수도 있습니다.
    return JsonFormatter(datefmt="%Y-%m-%d %H:%M:%S")


def test_format_with_string_message(json_formatter):
    """
    일반 문자열 메시지가 'message' 필드로 정상 포맷팅되는지 검증합니다.
    """
    record = logging.LogRecord(
        name="test_logger", level=logging.INFO, pathname="", lineno=0,
        msg="This is a simple string message", args=(), exc_info=None
    )
    
    result = json_formatter.format(record)
    parsed = json.loads(result)
    
    assert parsed["level"] == "INFO"
    assert parsed["name"] == "test_logger"
    assert parsed["message"] == "This is a simple string message"
    assert "data" not in parsed
    assert "timestamp" in parsed


def test_format_with_dict_message(json_formatter):
    """
    딕셔너리 형태의 메시지가 'data' 필드로 정상 포맷팅되는지 검증합니다.
    """
    dict_msg = {"action": "trade", "price": 50000}
    record = logging.LogRecord(
        name="test_logger", level=logging.DEBUG, pathname="", lineno=0,
        msg=dict_msg, args=(), exc_info=None
    )
    
    result = json_formatter.format(record)
    parsed = json.loads(result)
    
    assert parsed["level"] == "DEBUG"
    assert parsed["name"] == "test_logger"
    assert parsed["data"] == dict_msg
    assert "message" not in parsed


def test_format_with_exception(json_formatter):
    """
    예외 정보(exc_info)가 있을 경우 'exc_info' 필드에 트레이스백이 포함되는지 검증합니다.
    """
    try:
        1 / 0
    except ZeroDivisionError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test_logger", level=logging.ERROR, pathname="", lineno=0,
        msg="An error occurred", args=(), exc_info=exc_info
    )
    
    result = json_formatter.format(record)
    parsed = json.loads(result)
    
    assert parsed["level"] == "ERROR"
    assert parsed["message"] == "An error occurred"
    assert "exc_info" in parsed
    assert "ZeroDivisionError: division by zero" in parsed["exc_info"]
    assert "Traceback" in parsed["exc_info"]


def test_format_fallback_to_str(json_formatter):
    """
    orjson이 기본적으로 직렬화할 수 없는 객체(Custom Class 등)가 포함되었을 때, 
    default=str 폴백을 통해 문자열로 정상 직렬화되는지 검증합니다.
    """
    class CustomObject:
        def __str__(self):
            return "custom_string_representation"

    dict_msg = {"obj": CustomObject()}
    record = logging.LogRecord(
        name="test_logger", level=logging.INFO, pathname="", lineno=0,
        msg=dict_msg, args=(), exc_info=None
    )
    
    result = json_formatter.format(record)
    parsed = json.loads(result)
    
    # CustomObject가 __str__ 메서드의 결과로 직렬화되었는지 확인
    assert parsed["data"]["obj"] == "custom_string_representation"