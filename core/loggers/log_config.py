import os
import logging
from datetime import datetime

# --- 환경 변수 설정 ---
# OS 환경변수에서 APP_ENV 읽기 (기본값 dev)
APP_ENV = os.getenv("APP_ENV", "dev")

# --- Log Rotation Constants ---
LOG_LEVEL = logging.INFO if APP_ENV == "prod" else logging.DEBUG
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 30 if APP_ENV == "prod" else 50
LOG_COMPRESS_DAYS = 3 if APP_ENV == "prod" else 2
LOG_DELETE_DAYS = 30 if APP_ENV == "prod" else 50

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
