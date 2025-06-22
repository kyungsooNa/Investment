import unittest
from datetime import datetime
import pytz
from core.time_manager import TimeManager

class TestTimeManager(unittest.TestCase):
    def setUp(self):
        self.tm = TimeManager()

    def test_is_market_open_true(self):
        tz = pytz.timezone("Asia/Seoul")
        dt = tz.localize(datetime(2024, 4, 1, 10, 0, 0))  # 평일 오전 10시
        self.assertTrue(self.tm.is_market_open(dt))

    def test_is_market_open_false_weekend(self):
        dt = datetime(2024, 4, 6, 10, 0, 0)  # 토요일
        self.assertFalse(self.tm.is_market_open(dt))

    def test_is_market_open_false_after_hours(self):
        tz = pytz.timezone("Asia/Seoul")
        dt = tz.localize(datetime(2024, 4, 1, 16, 0, 0))  # 평일 오후 4시
        self.assertFalse(self.tm.is_market_open(dt))