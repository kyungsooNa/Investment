import unittest
import asyncio
from unittest.mock import MagicMock, patch, mock_open
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


class TestPerformanceManagerProfile(unittest.TestCase):
    def setUp(self):
        self.mock_logger = MagicMock()

    @patch('core.performance_manager.HAS_PYINSTRUMENT', True)
    @patch('core.performance_manager.pyinstrument', create=True)
    def test_profile_sync_enabled(self, mock_pyinstrument):
        """활성화 시 profile 컨텍스트 매니저가 프로파일링을 수행하는지 테스트"""
        mock_profiler = MagicMock()
        mock_profiler.output_text.return_value = "mock profile output"
        mock_profiler.output_html.return_value = "<html>mock</html>"
        mock_pyinstrument.Profiler.return_value = mock_profiler

        pm = PerformanceManager(logger=self.mock_logger, enabled=True)

        with patch('builtins.open', mock_open()):
            with patch('os.makedirs'):
                with pm.profile("test_block", save_html=False) as profiler:
                    _ = sum(range(100))

        mock_profiler.start.assert_called_once()
        mock_profiler.stop.assert_called_once()
        mock_profiler.output_text.assert_called_once()
        self.mock_logger.info.assert_called()

    def test_profile_sync_disabled(self):
        """비활성화 시 profile이 프로파일링 없이 통과하는지 테스트"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=False)

        with pm.profile("test_block") as profiler:
            _ = sum(range(100))

        self.assertIsNone(profiler)
        self.mock_logger.info.assert_not_called()

    @patch('core.performance_manager.HAS_PYINSTRUMENT', False)
    def test_profile_no_pyinstrument(self):
        """pyinstrument 미설치 시 경고 로그를 남기는지 테스트"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=True)

        with pm.profile("test_block"):
            _ = sum(range(100))

        self.mock_logger.warning.assert_called_once()
        warning_msg = self.mock_logger.warning.call_args[0][0]
        self.assertIn("pyinstrument", warning_msg)

    @patch('core.performance_manager.HAS_PYINSTRUMENT', True)
    @patch('core.performance_manager.pyinstrument', create=True)
    def test_profile_async_enabled(self, mock_pyinstrument):
        """활성화 시 profile_async가 async_mode로 프로파일링하는지 테스트"""
        mock_profiler = MagicMock()
        mock_profiler.output_text.return_value = "mock async profile"
        mock_profiler.output_html.return_value = "<html>mock</html>"
        mock_pyinstrument.Profiler.return_value = mock_profiler

        pm = PerformanceManager(logger=self.mock_logger, enabled=True)

        async def run():
            with patch('builtins.open', mock_open()):
                with patch('os.makedirs'):
                    async with pm.profile_async("async_block", save_html=False) as profiler:
                        await asyncio.sleep(0)

        asyncio.get_event_loop().run_until_complete(run())

        mock_pyinstrument.Profiler.assert_called_with(async_mode="enabled")
        mock_profiler.start.assert_called_once()
        mock_profiler.stop.assert_called_once()

    def test_profile_async_disabled(self):
        """비활성화 시 profile_async가 프로파일링 없이 통과하는지 테스트"""
        pm = PerformanceManager(logger=self.mock_logger, enabled=False)

        async def run():
            async with pm.profile_async("async_block"):
                await asyncio.sleep(0)

        asyncio.get_event_loop().run_until_complete(run())
        self.mock_logger.info.assert_not_called()

    @patch('core.performance_manager.HAS_PYINSTRUMENT', True)
    @patch('core.performance_manager.pyinstrument', create=True)
    def test_profile_saves_html(self, mock_pyinstrument):
        """save_html=True일 때 HTML 파일을 저장하는지 테스트"""
        mock_profiler = MagicMock()
        mock_profiler.output_text.return_value = "profile output"
        mock_profiler.output_html.return_value = "<html>report</html>"
        mock_pyinstrument.Profiler.return_value = mock_profiler

        pm = PerformanceManager(logger=self.mock_logger, enabled=True)

        m_open = mock_open()
        with patch('builtins.open', m_open):
            with patch('os.makedirs') as mock_makedirs:
                with pm.profile("html_test", save_html=True):
                    _ = 1 + 1

        mock_makedirs.assert_called_once_with(PerformanceManager.PROFILE_OUTPUT_DIR, exist_ok=True)
        m_open.assert_called_once()
        # 파일 경로에 html_test가 포함되어야 함
        filepath_arg = m_open.call_args[0][0]
        self.assertIn("html_test", filepath_arg)
        self.assertTrue(filepath_arg.endswith(".html"))
        # HTML 저장 로그 확인
        info_calls = [call[0][0] for call in self.mock_logger.info.call_args_list]
        html_saved_logs = [msg for msg in info_calls if "HTML 저장" in msg]
        self.assertEqual(len(html_saved_logs), 1)

    @patch('core.performance_manager.HAS_PYINSTRUMENT', True)
    @patch('core.performance_manager.pyinstrument', create=True)
    def test_profile_exception_still_stops(self, mock_pyinstrument):
        """블록 내 예외 발생 시에도 프로파일러가 정상 종료되는지 테스트"""
        mock_profiler = MagicMock()
        mock_profiler.output_text.return_value = "output"
        mock_profiler.output_html.return_value = "<html></html>"
        mock_pyinstrument.Profiler.return_value = mock_profiler

        pm = PerformanceManager(logger=self.mock_logger, enabled=True)

        with self.assertRaises(ValueError):
            with patch('builtins.open', mock_open()):
                with patch('os.makedirs'):
                    with pm.profile("exception_test", save_html=False):
                        raise ValueError("test error")

        mock_profiler.stop.assert_called_once()