import unittest
from core.logger import Logger

class TestLogger(unittest.TestCase):
    def test_logger_info(self):
        logger = Logger()
        logger.info("info test")

    def test_logger_error(self):
        logger = Logger()
        logger.error("error test")
