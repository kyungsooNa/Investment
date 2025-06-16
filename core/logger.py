# core/logger.py
import logging
import os
from datetime import datetime
import http.client


class Logger:
    """
    애플리케이션의 로깅을 관리하는 클래스입니다.
    운영에 필요한 정보(operational.log)와 디버깅에 필요한 데이터(debug.log)를 분리하여 저장합니다.
    매 실행마다 시간이 적힌 새로운 로그 파일을 생성합니다.
    """

    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir

        # 현재 실행 시간을 기반으로 로그 파일명에 사용할 타임스탬프 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 로그 디렉토리(logs/)가 없으면 생성
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

        # 로그 파일 경로 설정 (파일명에 타임스탬프 포함)
        # 파일명 형식을 YYYYMMDD_HHMMSS_debug.log, YYYYMMDD_HHMMSS_operational.log 로 변경
        self.operational_log_path = os.path.join(self.log_dir, f"{timestamp}_operational.log")  # <--- 파일명 형식 수정
        self.debug_log_path = os.path.join(self.log_dir, f"{timestamp}_debug.log")  # <--- 파일명 형식 수정

        # 로거 인스턴스 생성
        self.operational_logger = self._setup_logger('operational_logger', self.operational_log_path, logging.INFO)
        self.debug_logger = self._setup_logger('debug_logger', self.debug_log_path, logging.DEBUG)

        # 기존 로깅 핸들러 제거 및 urllib3 로거 레벨 설정 (중복 로깅 방지)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        http.client.HTTPConnection.debuglevel = 0  # HTTP 통신 디버그 레벨 비활성화

    def _setup_logger(self, name, log_file, level):
        """단일 로거를 설정합니다."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False  # 상위 로거로 전파 방지 (중복 출력 방지)

        # 파일 핸들러 설정
        # 'w': write mode, 매번 새로운 파일 생성 (매 실행마다 파일이 분리되므로)
        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

        return logger

    def info(self, message):
        """운영 및 디버깅 로그에 INFO 레벨 메시지를 기록합니다."""
        self.operational_logger.info(message)
        self.debug_logger.info(message)

    def debug(self, message):
        """디버깅 로그에 DEBUG 레벨 메시지를 기록합니다."""
        self.debug_logger.debug(message)

    def warning(self, message):
        """운영 및 디버깅 로그에 WARNING 레벨 메시지를 기록합니다."""
        self.operational_logger.warning(message)
        self.debug_logger.warning(message)

    def error(self, message):
        """운영 및 디버깅 로그에 ERROR 레벨 메시지를 기록합니다."""
        self.operational_logger.error(message)
        self.debug_logger.error(message)

    def critical(self, message):
        """운영 및 디버깅 로그에 CRITICAL 레벨 메시지를 기록합니다."""
        self.operational_logger.critical(message)
        self.debug_logger.critical(message)
