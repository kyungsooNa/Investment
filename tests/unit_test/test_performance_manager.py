import unittest
from unittest.mock import MagicMock, patch
from core.performance_manager import PerformanceManager

class TestPerformanceManager(unittest.TestCase):
    def setUp(self):
        self.mock_logger = MagicMock()

    def test_init_defaults(self):
        """기본 초기화 테스트: 비활성화 상태, 기본 로거 사용"""
        pm = PerformanceManager()
        self.assertFalse(pm.enabled)
        self.assertIsNotNone(pm.logger)

    def test_init_custom(self):
        """커스텀 초기화 테스트: 활성화 상태, 커스텀 로거 사용"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=True)
        self.assertTrue(pm.enabled)
        self.assertEqual(pm.logger, self.mock_logger)

    @patch('core.performance_manager.time.time')
    def test_start_timer_enabled(self, mock_time):
        """활성화 시 start_timer가 현재 시간을 반환하는지 테스트"""
        mock_time.return_value = 12345.0
        pm = PerformanceManager(enabled=True)
        start_time = pm.start_timer()
        self.assertEqual(start_time, 12345.0)

    def test_start_timer_disabled(self):
        """비활성화 시 start_timer가 0.0을 반환하는지 테스트"""
        pm = PerformanceManager(enabled=False)
        start_time = pm.start_timer()
        self.assertEqual(start_time, 0.0)

    @patch('core.performance_manager.time.time')
    def test_log_timer_enabled(self, mock_time):
        """활성화 시 log_timer가 경과 시간을 로깅하는지 테스트"""
        # start_time = 100, current_time = 105 -> duration = 5
        mock_time.return_value = 105.0
        pm = PerformanceManager(logger=self.mock_logger, enabled=True)
        
        pm.log_timer("test_func", 100.0)
        
        self.mock_logger.info.assert_called_once()
        args, _ = self.mock_logger.info.call_args
        self.assertIn("[Performance] test_func: 5.0000s", args[0])

    @patch('core.performance_manager.time.time')
    def test_log_timer_with_extra_info(self, mock_time):
        """extra_info가 포함된 경우 로깅 포맷 테스트"""
        mock_time.return_value = 105.0
        pm = PerformanceManager(logger=self.mock_logger, enabled=True)
        
        pm.log_timer("test_func", 100.0, extra_info="details")
        
        self.mock_logger.info.assert_called_once()
        args, _ = self.mock_logger.info.call_args
        self.assertIn("[Performance] test_func: 5.0000s (details)", args[0])

    def test_log_timer_disabled(self):
        """비활성화 시 log_timer가 로깅하지 않는지 테스트"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=False)
        pm.log_timer("test_func", 100.0)
        self.mock_logger.info.assert_not_called()

    def test_log_timer_start_time_zero(self):
        """start_time이 0.0일 때(타이머 시작 실패 등) 로깅하지 않는지 테스트"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=True)
        pm.log_timer("test_func", 0.0)
        self.mock_logger.info.assert_not_called()

    def test_init_with_threshold(self):
        """임계값 설정 초기화 테스트"""
        pm = PerformanceManager(threshold=0.5)
        self.assertEqual(pm.threshold, 0.5)

    @patch('core.performance_manager.time.time')
    def test_log_timer_below_threshold(self, mock_time):
        """임계값 미만일 때 로깅하지 않는지 테스트"""
        # start=100, current=100.4 -> duration=0.4 < threshold=0.5
        mock_time.return_value = 100.4
        pm = PerformanceManager(logger=self.mock_logger, enabled=True, threshold=0.5)
        
        pm.log_timer("test_func", 100.0)
        
        self.mock_logger.info.assert_not_called()

    @patch('core.performance_manager.time.time')
    def test_log_timer_above_threshold(self, mock_time):
        """임계값 이상일 때 로깅하는지 테스트"""
        # start=100, current=100.6 -> duration=0.6 >= threshold=0.5
        mock_time.return_value = 100.6
        pm = PerformanceManager(logger=self.mock_logger, enabled=True, threshold=0.5)
        
        pm.log_timer("test_func", 100.0)
        
        self.mock_logger.info.assert_called_once()
        args, _ = self.mock_logger.info.call_args
        self.assertIn("[Performance] test_func: 0.6000s", args[0])

    @patch('core.performance_manager.time.time')
    def test_log_timer_override_threshold(self, mock_time):
        """호출 시 임계값 오버라이드 테스트"""
        # 기본 threshold=1.0, 오버라이드=0.1
        # duration=0.5 -> 기본값(1.0) 미만이지만 오버라이드(0.1) 이상이므로 로깅되어야 함
        mock_time.return_value = 100.5
        pm = PerformanceManager(logger=self.mock_logger, enabled=True, threshold=1.0)
        
        pm.log_timer("test_func", 100.0, threshold=0.1)
        
        self.mock_logger.info.assert_called_once()
        args, _ = self.mock_logger.info.call_args
        self.assertIn("[Performance] test_func: 0.5000s", args[0])