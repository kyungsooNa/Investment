# core/logger.py
import logging
import os
import time
from datetime import datetime
import http.client
import inspect
import json
from logging.handlers import RotatingFileHandler

# --- Log Rotation Constants ---
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 20

# --- Timestamp Singleton ---
_log_timestamp = None

def get_log_timestamp():
    """애플리케이션 실행 당 한 번만 타임스탬프를 생성하고, 이후에는 동일한 값을 반환합니다."""
    global _log_timestamp
    if _log_timestamp is None:
        _log_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _log_timestamp

def reset_log_timestamp_for_test():
    """테스트 격리를 위해 전역 타임스탬프를 리셋합니다."""
    global _log_timestamp
    _log_timestamp = None

def _log_rotation_namer(default_name):
    """
    RotatingFileHandler의 백업 파일 이름을 변경하는 namer 함수.
    기본 동작인 'filename.log.1' 대신 'filename_1.log' 형태로 변경합니다.
    """
    # default_name: /path/to/file.log.1
    base_path, backup_num_ext = os.path.splitext(default_name)
    
    # 백업 번호 확인 (.1, .2 등)
    if len(backup_num_ext) > 1 and backup_num_ext[1:].isdigit():
        backup_num = backup_num_ext[1:]
        # 원본 파일명에서 확장자 분리
        file_root, file_ext = os.path.splitext(base_path)
        return f"{file_root}_{backup_num}{file_ext}"
    return default_name
# -------------------------


class JsonFormatter(logging.Formatter):
    """
    로그 레코드를 JSON 형식으로 변환하는 포맷터.
    """
    def format(self, record):
        log_object = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
        }
        # message가 dict 형태이면, 그대로 data 필드로 추가
        if isinstance(record.msg, dict):
            log_object["data"] = record.msg
        else:
            log_object["message"] = record.getMessage()

        # 예외 정보가 있으면 추가
        if record.exc_info:
            log_object['exc_info'] = self.formatException(record.exc_info)

        return json.dumps(log_object, ensure_ascii=False)


def get_strategy_logger(strategy_name: str, log_dir="logs", sub_dir: str = None):
    """
    전략별 전용 로거를 생성하고 반환합니다.
    - 실행 시마다 타임스탬프가 찍힌 JSON 파일 핸들러 생성
    - 콘솔 스트림 핸들러
    """
    logger = logging.getLogger(f"strategy.{strategy_name}")
    
    if logger.handlers:
        # 이미 핸들러가 설정된 경우, 새 실행을 위해 기존 핸들러를 제거하고 다시 설정
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    strategy_log_dir = os.path.join(log_dir, "strategies")
    if sub_dir:
        strategy_log_dir = os.path.join(strategy_log_dir, sub_dir)
    if not os.path.exists(strategy_log_dir):
        os.makedirs(strategy_log_dir, exist_ok=True)

    timestamp = get_log_timestamp()
    
    # 1. JSON 파일 핸들러 (실행마다 새로 생성)
    log_file = os.path.join(strategy_log_dir, f"{timestamp}_{strategy_name}.log.json")
    file_handler = RotatingFileHandler(
        log_file,
        mode='a',
        encoding='utf-8',
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.namer = _log_rotation_namer
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    return logger


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
        self.debug_logger = self._setup_logger('debug_logger', self.debug_log_path, logging.DEBUG)

        # 기존 로깅 핸들러 제거 및 urllib3 로거 레벨 설정 (중복 로깅 방지)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
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

        file_handler = RotatingFileHandler(
            log_file,
            mode=mode,
            encoding='utf-8',
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT
        )
        file_handler.namer = _log_rotation_namer
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

        return logger

    def info(self, message):
        self.operational_logger.info(message)
        self.debug_logger.info(message)

    def debug(self, message):
        self.debug_logger.debug(message)

    def warning(self, message):
        self.operational_logger.warning(message)
        self.debug_logger.warning(message)

    def error(self, message, exc_info=False):
        self.operational_logger.error(message, exc_info=exc_info)
        caller_info = self._get_caller_info()
        full_message = f"{caller_info} - {message}"
        self.debug_logger.error(full_message, exc_info=exc_info)

    def critical(self, message, exc_info=False):
        self.operational_logger.critical(message, exc_info=exc_info)
        caller_info = self._get_caller_info()
        full_message = f"{caller_info} - {message}"
        self.debug_logger.critical(full_message, exc_info=exc_info)

    def exception(self, message):
        """
        예외 정보를 포함하여 ERROR 레벨로 로그를 남깁니다.
        주로 except 블록 안에서 사용합니다.
        """
        self.error(message, exc_info=True)

    def _get_caller_info(self):
        frame = inspect.currentframe()
        while frame:
            info = inspect.getframeinfo(frame)
            if "logger.py" not in info.filename:
                filename = os.path.basename(info.filename)
                return f"{filename}:{info.lineno}"
            frame = frame.f_back
        return "unknown"