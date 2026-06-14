# core/logger.py
import logging
import os
import queue
from logging.handlers import QueueHandler, QueueListener

from core.loggers.log_config import (
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    LOG_LEVEL,
    get_log_timestamp,
    get_strategy_log_date,
    reset_log_timestamp_for_test
)
from core.loggers.size_time_rotating_file_handler import SizeTimeRotatingFileHandler
from core.loggers.json_formatter import JsonFormatter
from core.loggers.streaming_event_logger import StreamingEventLogger
from core.loggers.cache_event_logger import CacheEventLogger
from core.loggers.strategy_info_filter import StrategyInfoFilter
from core.loggers.app_logger import Logger
from core.loggers.async_handler import DictPreservingQueueHandler

_active_listeners = []

def _resolve_log_dir(log_dir: str) -> str:
    """테스트 실행 시 기본 로그 경로를 격리된 임시 경로로 우회한다."""
    if log_dir == "logs":
        return os.getenv("INVESTMENT_LOG_DIR", log_dir)
    return log_dir

def _clear_logger_handlers(logger: logging.Logger) -> None:
    """기존 핸들러를 제거해 전역 logger 재사용 시 stale 상태를 끊는다."""
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

def setup_async_logger(logger: logging.Logger, file_handler: logging.Handler):
    """파일 I/O를 백그라운드 스레드로 위임하는 비동기 큐 세팅"""
    log_queue = queue.Queue(-1)
    queue_handler = DictPreservingQueueHandler(log_queue)
    logger.addHandler(queue_handler)

    listener = QueueListener(log_queue, file_handler, respect_handler_level=True)
    listener.start()
    _active_listeners.append(listener)
    return listener

def shutdown_logging():
    """등록된 모든 비동기 로거의 큐 처리를 완료하고 리스너와 핸들러를 안전하게 종료합니다."""
    for listener in _active_listeners:
        listener.queue.join()
        listener.stop()
        for h in listener.handlers:
            h.flush()
            h.close()
    _active_listeners.clear()

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
    log_dir = _resolve_log_dir(log_dir)
    streaming_log_dir = os.path.join(log_dir, "streaming")
    os.makedirs(streaming_log_dir, exist_ok=True)

    logger_name = "streaming_event"
    logger = logging.getLogger(logger_name)

    expected_dir = os.path.abspath(streaming_log_dir)
    if logger.handlers and getattr(logger, "_streaming_log_dir", None) == expected_dir:
        return StreamingEventLogger(logger)
    if logger.handlers:
        _clear_logger_handlers(logger)

    logger.setLevel(LOG_LEVEL)
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
    setup_async_logger(logger, handler)
    logger._streaming_log_dir = expected_dir

    return StreamingEventLogger(logger)

def get_cache_event_logger(log_dir: str = "logs") -> "CacheEventLogger":
    """
    캐시 동작 전용 이벤트 로거를 생성하고 반환합니다.
    경로: logs/cache/{timestamp}_cache.log.json

    로그 항목 구조:
      - action: 아래 CacheEventLogger 참조
      + action별 세부 필드 (code, caller, before_price, after_price, ohlcv_count, ...)
    """
    log_dir = _resolve_log_dir(log_dir)
    cache_log_dir = os.path.join(log_dir, "cache")
    os.makedirs(cache_log_dir, exist_ok=True)

    logger_name = "cache_event"
    logger = logging.getLogger(logger_name)

    expected_dir = os.path.abspath(cache_log_dir)
    if logger.handlers and getattr(logger, "_cache_log_dir", None) == expected_dir:
        return CacheEventLogger(logger)
    if logger.handlers:
        _clear_logger_handlers(logger)

    logger.setLevel(LOG_LEVEL)
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
    setup_async_logger(logger, handler)
    logger._cache_log_dir = expected_dir

    return CacheEventLogger(logger)

def get_strategy_logger(strategy_name: str, log_dir="logs", sub_dir: str = None):
    """
    전략별 전용 로거를 생성하고 반환합니다.

    - 파일명은 날짜(YYYYMMDD)로 고정되어, 같은 날의 모든 프로세스/호출이 동일 파일에 append 된다.
    - 같은 프로세스 내에서 동일 (strategy_name, sub_dir) 조합으로 재호출되면 멱등하게 기존 logger를 반환한다
      (핸들러 중복 생성으로 인한 신규 _N 파일 누적 방지).
    - 활성 파일이 maxBytes 를 넘으면 SizeTimeRotatingFileHandler 가 자동으로 _N+1 로 롤오버한다.
    """
    log_dir = _resolve_log_dir(log_dir)
    logger_key = f"strategy.{strategy_name}"
    if sub_dir:
        logger_key = f"{logger_key}.{sub_dir}"
    logger = logging.getLogger(logger_key)

    strategy_log_dir = os.path.join(log_dir, "strategies")
    if sub_dir:
        strategy_log_dir = os.path.join(strategy_log_dir, sub_dir)
    expected_dir = os.path.abspath(strategy_log_dir)

    # 같은 (logger_name, log_dir) 조합으로 재호출되면 기존 logger를 그대로 재사용한다.
    # log_dir 이 다르면 (테스트의 tmp_path 격리 등) 기존 핸들러를 정리하고 재구성한다.
    if logger.handlers and getattr(logger, "_strategy_log_dir", None) == expected_dir:
        return logger
    if logger.handlers:
        for h in logger.handlers[:]:
            h.close()
            logger.removeHandler(h)

    logger.setLevel(LOG_LEVEL)
    logger.propagate = True

    if not os.path.exists(strategy_log_dir):
        os.makedirs(strategy_log_dir, exist_ok=True)

    log_date = get_strategy_log_date()
    log_file = os.path.join(strategy_log_dir, f"{log_date}_{strategy_name}.log.json")
    file_handler = SizeTimeRotatingFileHandler(
        log_file,
        mode='a',
        encoding='utf-8',
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        append_to_latest=True,
    )
    file_handler.setFormatter(JsonFormatter())
    setup_async_logger(logger, file_handler)
    logger._strategy_log_dir = expected_dir

    return logger

def get_performance_logger(log_dir="logs"):
    """
    성능 측정 전용 로거를 생성하고 반환합니다.
    경로: logs/performance/{timestamp}_perf.log
    """
    log_dir = _resolve_log_dir(log_dir)
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
    setup_async_logger(logger, file_handler)

    return logger
