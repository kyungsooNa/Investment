import os
from datetime import datetime

# --- Log Rotation Constants ---
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 30

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
