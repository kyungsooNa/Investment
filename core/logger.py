# core/logger.py
import logging
import os
from datetime import datetime

class Logger:
    """
    애플리케이션의 로깅을 관리하는 클래스입니다.
    운영에 필요한 정보(operational.log)와 디버깅에 필요한 데이터(debug.log)를 분리하여 저장합니다.
    """
    def __init__(self, log_dir="logs", operational_log_name="operational.log", debug_log_name="debug.log"):
        """
        로거를 초기화하고 두 가지 유형의 파일 핸들러를 설정합니다.
        :param log_dir: 로그 파일이 저장될 디렉토리
        :param operational_log_name: 운영 로그 파일 이름
        :param debug_log_name: 디버깅 로그 파일 이름
        """
        self.log_dir = log_dir
        # 로그 디렉토리가 없으면 생성
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

        # 로그 파일 경로 설정
        self.operational_log_path = os.path.join(self.log_dir, operational_log_name)
        self.debug_log_path = os.path.join(self.log_dir, debug_log_name)

        # 로거 인스턴스 생성
        self.operational_logger = self._setup_logger('operational_logger', self.operational_log_path, logging.INFO)
        self.debug_logger = self._setup_logger('debug_logger', self.debug_log_path, logging.DEBUG)

        # Python의 기본 로깅 설정을 비활성화하여 중복 로깅 방지
        # logging.basicConfig(level=logging.WARNING) # 이전에 main.py나 env.py 등에서 설정되었다면 제거
        # urllib3.connectionpool의 DEBUG 로깅도 직접 처리하기 위해 기본 설정 변경
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)


    def _setup_logger(self, name, log_file, level):
        """단일 로거를 설정합니다."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False # 상위 로거로 전파 방지 (중복 출력 방지)

        # 파일 핸들러 설정
        # 'a': append mode, 로그 파일이 이미 있으면 이어서 작성
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

        # 콘솔 핸들러 (선택 사항: 디버그 모드에서만 콘솔 출력하고 싶을 경우)
        # console_handler = logging.StreamHandler()
        # console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        # logger.addHandler(console_handler)

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

# 전역 로거 인스턴스 (필요 시 다른 모듈에서 직접 임포트하여 사용)
# 그러나 이 아키텍처에서는 TradingApp에서 생성하여 하위 계층으로 전달하는 것이 더 권장됨
# global_logger = Logger()
