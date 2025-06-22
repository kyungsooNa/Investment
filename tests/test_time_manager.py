from core.time_manager import TimeManager
from unittest.mock import patch
from datetime import datetime
import pytz

def test_get_current_kst_time_returns_timezone_aware_datetime():
    manager = TimeManager()
    current_time = manager.get_current_kst_time()

    assert current_time.tzinfo is not None
    assert str(current_time.tzinfo) == "Asia/Seoul"

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_true(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 21, 10, 0, 0))
    manager = TimeManager()
    assert manager.is_market_open() is True

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_false_weekend(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 22, 10, 0, 0))  # 토요일
    manager = TimeManager()
    assert manager.is_market_open() is False

@patch("core.time_manager.TimeManager.get_current_kst_time")
def test_is_market_open_false_after_hours(mock_get_time):
    kst = pytz.timezone("Asia/Seoul")
    mock_get_time.return_value = kst.localize(datetime(2024, 6, 21, 16, 0, 0))  # 평일 16시
    manager = TimeManager()
    assert manager.is_market_open() is False
