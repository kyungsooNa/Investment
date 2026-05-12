import logging
import os
import time
import http.client
import queue
from logging.handlers import QueueHandler, QueueListener

from core.loggers.log_config import get_log_timestamp, LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOG_LEVEL
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.strategy_info_filter import StrategyInfoFilter
from core.loggers.async_handler import DictPreservingQueueHandler

class Logger:
    """
    애플리케이션의 로깅을 관리하는 클래스입니다.
    운영에 필요한 정보(operational.log)와 디버깅에 필요한 데이터(debug.log)를 분리하여 저장합니다.
    매 실행마다 시간이 적힌 새로운 로그 파일을 생성합니다.
    """

    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        self.common_log_dir = os.path.join(self.log_dir, "common")
        self.strategy_log_dir = os.path.join(self.log_dir, "strategies")
        self._listeners = []

        # 공유 타임스탬프 생성
        timestamp = get_log_timestamp()

        # 로그 디렉토리(logs/, logs/common, logs/strategies)가 없으면 생성
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
        if not os.path.exists(self.common_log_dir):
            os.makedirs(self.common_log_dir, exist_ok=True)
        if not os.path.exists(self.strategy_log_dir):
            os.makedirs(self.strategy_log_dir, exist_ok=True)

        # 오래된 로그 파일 정리 (30일 경과)
        self._cleanup_old_logs(days=30)

        # 로그 파일 경로 설정 (logs/common/ 하위)
        self.operational_log_path = os.path.join(self.common_log_dir, f"{timestamp}_operational.log")
        self.debug_log_path = os.path.join(self.common_log_dir, f"{timestamp}_debug.log")

        # 로거 인스턴스 생성
        self.operational_logger = self._setup_logger('operational_logger', self.operational_log_path, logging.INFO)
        self.debug_logger = self._setup_logger('debug_logger', self.debug_log_path, LOG_LEVEL)

        # 전략 로그 필터 생성 (debug.log 용량 관리용)
        strategy_filter = StrategyInfoFilter()

        # 기존 로깅 핸들러 제거 및 urllib3 로거 레벨 설정 (중복 로깅 방지)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        # 루트 로거에 통합 로그 핸들러 연결 (전략 로거 등 전파된 로그 수집)
        root_logger = logging.getLogger()
        root_logger.setLevel(LOG_LEVEL)
        for h in self.debug_logger.handlers:
            h.addFilter(strategy_filter)  # 전략 로그는 INFO 이상만 debug.log에 기록
            root_logger.addHandler(h)
        for h in self.operational_logger.handlers:
            root_logger.addHandler(h)

        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        logging.getLogger('aiosqlite').setLevel(logging.WARNING)
        http.client.HTTPConnection.debuglevel = 0  # HTTP 통신 디버그 레벨 비활성화

    def _cleanup_old_logs(self, days=30):
        """
        로그 디렉토리를 순회하며 지정된 일수(days)보다 오래된 파일을 삭제합니다.
        """
        now = time.time()
        cutoff = now - (days * 86400)

        for root, _, files in os.walk(self.log_dir):
            for filename in files:
                # 로그 파일 확장자 또는 패턴 확인
                if ".log" in filename or ".json" in filename:
                    file_path = os.path.join(root, filename)
                    try:
                        if os.path.getmtime(file_path) < cutoff:
                            os.remove(file_path)
                    except Exception:
                        pass  # 삭제 실패(권한 문제, 파일 잠김 등) 시 무시

    def _setup_logger(self, name, log_file, level, mode='a'):
        """단일 로거를 설정합니다."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False

        if logger.handlers:
            return logger

        file_handler = SizeTimeRotatingFileHandler(
            log_file,
            mode=mode,
            encoding='utf-8',
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
        
        log_queue = queue.Queue(-1)
        queue_handler = DictPreservingQueueHandler(log_queue)
        logger.addHandler(queue_handler)

        listener = QueueListener(log_queue, file_handler, respect_handler_level=True)
        listener.start()
        self._listeners.append(listener)

        return logger

    def info(self, message, *args):
        self.operational_logger.info(message, *args, stacklevel=2)
        self.debug_logger.info(message, *args, stacklevel=2)

    def debug(self, message, *args):
        self.debug_logger.debug(message, *args, stacklevel=2)

    def warning(self, message, *args, exc_info=False):
        self.operational_logger.warning(message, *args, exc_info=exc_info, stacklevel=2)
        self.debug_logger.warning(message, *args, exc_info=exc_info, stacklevel=2)

    def error(self, message, *args, exc_info=False):
        self.operational_logger.error(message, *args, exc_info=exc_info, stacklevel=2)
        self.debug_logger.error(message, *args, exc_info=exc_info, stacklevel=2)

    def critical(self, message, *args, exc_info=False):
        self.operational_logger.critical(message, *args, exc_info=exc_info, stacklevel=2)
        self.debug_logger.critical(message, *args, exc_info=exc_info, stacklevel=2)

    def exception(self, message):
        """
        예외 정보를 포함하여 ERROR 레벨로 로그를 남깁니다.
        주로 except 블록 안에서 사용합니다.
        """
        self.error(message, exc_info=True)

    def flush(self):
        """비동기 큐의 남은 로그를 모두 처리하고 파일에 플러시합니다."""
        for listener in self._listeners:
            listener.queue.join()
            for fh in listener.handlers:
                fh.flush()

    def close(self):
        for listener in self._listeners:
            listener.stop()
            for handler in listener.handlers:
                handler.close()
