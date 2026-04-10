# core/logger.py
import logging
import os

from core.loggers.log_config import (
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    get_log_timestamp,
    reset_log_timestamp_for_test
)
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.json_formatter import JsonFormatter
from core.loggers.streaming_event_logger import StreamingEventLogger
from core.loggers.cache_event_logger import CacheEventLogger
from core.loggers.strategy_info_filter import StrategyInfoFilter
from core.loggers.app_logger import Logger

def get_streaming_logger(log_dir: str = "logs") -> "StreamingEventLogger":
    """
    실시간 스트리밍 전용 이벤트 로거를 생성하고 반환합니다.
    경로: logs/streaming/{timestamp}_streaming.log.json

    로그 항목 구조:
      - action: "subscribe" | "unsubscribe" | "summary" |
                "connect" | "disconnect" |
                "reconnect" | "restore" |
                "pt_subscribe"  | "pt_unsubscribe"  (프로그램매매 H0STPGM0)
                "price_subscribe" | "price_unsubscribe" (현재 체결가 H0STCNT0)
      + action별 세부 필드 (code, categories, reason, trigger, ...)
    """
    streaming_log_dir = os.path.join(log_dir, "streaming")
    os.makedirs(streaming_log_dir, exist_ok=True)

    logger_name = "streaming_event"
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        timestamp = get_log_timestamp()
        log_file = os.path.join(streaming_log_dir, f"{timestamp}_streaming.log.json")
        handler = SizeTimeRotatingFileHandler(
            log_file,
            mode="a",
            encoding="utf-8",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)

    return StreamingEventLogger(logger)

def get_cache_event_logger(log_dir: str = "logs") -> "CacheEventLogger":
    """
    캐시 동작 전용 이벤트 로거를 생성하고 반환합니다.
    경로: logs/cache/{timestamp}_cache.log.json

    로그 항목 구조:
      - action: 아래 CacheEventLogger 참조
      + action별 세부 필드 (code, caller, before_price, after_price, ohlcv_count, ...)
    """
    cache_log_dir = os.path.join(log_dir, "cache")
    os.makedirs(cache_log_dir, exist_ok=True)

    logger_name = "cache_event"
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        timestamp = get_log_timestamp()
        log_file = os.path.join(cache_log_dir, f"{timestamp}_cache.log.json")
        handler = SizeTimeRotatingFileHandler(
            log_file,
            mode="a",
            encoding="utf-8",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)

    return CacheEventLogger(logger)

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

    logger.setLevel(logging.DEBUG)
    logger.propagate = True

    strategy_log_dir = os.path.join(log_dir, "strategies")
    if sub_dir:
        strategy_log_dir = os.path.join(strategy_log_dir, sub_dir)
    if not os.path.exists(strategy_log_dir):
        os.makedirs(strategy_log_dir, exist_ok=True)

    timestamp = get_log_timestamp()
    
    # 1. JSON 파일 핸들러 (실행마다 새로 생성)
    log_file = os.path.join(strategy_log_dir, f"{timestamp}_{strategy_name}.log.json")
    file_handler = SizeTimeRotatingFileHandler(
        log_file,
        mode='a',
        encoding='utf-8',
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    return logger

def get_performance_logger(log_dir="logs"):
    """
    성능 측정 전용 로거를 생성하고 반환합니다.
    경로: logs/performance/{timestamp}_perf.log
    """
    logger = logging.getLogger("performance")
    
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    perf_log_dir = os.path.join(log_dir, "performance")
    if not os.path.exists(perf_log_dir):
        os.makedirs(perf_log_dir, exist_ok=True)

    timestamp = get_log_timestamp()
    log_file = os.path.join(perf_log_dir, f"{timestamp}_perf.log")
    
    file_handler = SizeTimeRotatingFileHandler(
        log_file,
        mode='a',
        encoding='utf-8',
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    logger.addHandler(file_handler)

    return logger
