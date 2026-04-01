# core/logger.py
import logging
import os
import time
import glob
from datetime import datetime
import http.client
import json
from logging.handlers import RotatingFileHandler

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

class SizeTimeRotatingFileHandler(RotatingFileHandler):
    """
    파일 크기가 maxBytes를 초과하면 인덱스를 붙여 새 파일로 교체하는 핸들러.
    인덱스가 클수록 최신 파일입니다.
    예: app_1.log (가장 오래됨) ... app_25.log (오래된 백업) -> app_26.log (현재 활성)
    """
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
        # 확장자 처리 (예: .log.json)
        if filename.endswith(".log.json"):
            root, ext = filename[:-len(".log.json")], ".log.json"
        else:
            root, ext = os.path.splitext(filename)

        self._log_root = root
        self._log_ext = ext

        # 기존 인덱스 파일 중 최대 인덱스 탐색
        pattern = f"{glob.escape(root)}_[0-9]*{glob.escape(ext)}"
        max_index = 0
        for f in glob.glob(pattern):
            try:
                idx_str = f[:-len(ext)].split('_')[-1]
                if idx_str.isdigit():
                    max_index = max(max_index, int(idx_str))
            except (ValueError, IndexError):
                continue

        # 초기 활성 파일은 max_index + 1 번 인덱스로 생성
        initial_filename = f"{root}_{max_index + 1}{ext}"
        super().__init__(initial_filename, mode=mode, maxBytes=maxBytes,
                         backupCount=backupCount, encoding=encoding, delay=delay)

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # 현재 존재하는 인덱스 파일 중 최대 인덱스 결정
        pattern = f"{glob.escape(self._log_root)}_[0-9]*{glob.escape(self._log_ext)}"
        existing = glob.glob(pattern)

        max_index = 0
        for f in existing:
            try:
                idx_str = f[:-len(self._log_ext)].split('_')[-1]
                if idx_str.isdigit():
                    max_index = max(max_index, int(idx_str))
            except (ValueError, IndexError):
                continue

        # baseFilename을 다음 인덱스 파일로 업데이트 (이것이 새 활성 파일이 됨)
        next_filename = f"{self._log_root}_{max_index + 1}{self._log_ext}"
        self.baseFilename = os.path.abspath(next_filename)

        # 오래된 파일 삭제 (backupCount 초과 시)
        if self.backupCount > 0:
            all_files = glob.glob(pattern)
            all_files.sort(key=lambda f: int(f[:-len(self._log_ext)].split('_')[-1])
                           if f[:-len(self._log_ext)].split('_')[-1].isdigit() else -1)
            if len(all_files) > self.backupCount:
                for f in all_files[:len(all_files) - self.backupCount]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass

        if not self.delay:
            self.stream = self._open()


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

        return json.dumps(log_object, ensure_ascii=False, default=str)


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


class StreamingEventLogger:
    """
    실시간 구독·연결 이벤트를 JSON으로 기록하는 전용 로거.

    logs/streaming/{timestamp}_streaming.log.json 에 기록한다.
    각 항목은 JsonFormatter를 통해 {"timestamp":..., "level":..., "data":{...}} 형태로 저장된다.

    --- 로그 종류 ---
    [통합 가격 구독 - PriceSubscriptionService (H0UNCNT0)]
      log_subscribe   : 통합 현재가 구독 등록 (categories = 구독 요청자 목록)
      log_unsubscribe : 통합 현재가 구독 해제
      log_summary     : 현재 전체 구독 현황 스냅샷

    [연결 이벤트 - StreamingService]
      log_connect     : WebSocket 연결 성공
      log_disconnect  : WebSocket 연결 해제

    [워치독/복원 이벤트 - WebSocketWatchdogTask]
      log_reconnect   : 강제 재연결 (trigger 포함)
      log_restore     : 앱 시작 시 구독 상태 복원

    [프로그램매매 구독 - WebSocketWatchdogTask (H0STPGM0)]
      log_pt_subscribe   : 프로그램매매 구독 등록
      log_pt_unsubscribe : 프로그램매매 구독 해제

    [실시간 체결가 구독 - WebSocketWatchdogTask (H0STCNT0)]
      log_price_subscribe   : 실시간 현재 체결가 구독 등록
      log_price_unsubscribe : 실시간 현재 체결가 구독 해제
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    # ── PriceSubscriptionService 이벤트 (H0UNCNT0) ──────────────

    def log_subscribe(self, code: str, categories: dict, active_count: int) -> None:
        """통합 현재가 구독 등록.

        Args:
            code: 종목코드
            categories: {category_key: priority_int} — 해당 종목을 요청한 카테고리 목록
                        ex) {"portfolio": 1, "strategy_momentum": 2}
            active_count: 구독 등록 후 총 활성 구독 수
        """
        self._logger.info({
            "action": "subscribe",
            "code": code,
            "categories": {k: int(v) for k, v in categories.items()},
            "active_count": active_count,
        })

    def log_unsubscribe(self, code: str, active_count: int) -> None:
        """통합 현재가 구독 해제.

        Args:
            code: 종목코드
            active_count: 구독 해제 후 총 활성 구독 수
        """
        self._logger.info({
            "action": "unsubscribe",
            "code": code,
            "active_count": active_count,
        })

    def log_summary(
        self,
        active_count: int,
        active_codes: list,
        pending_by_priority: dict,
    ) -> None:
        """현재 구독 상태 전체 요약.

        Args:
            active_count: 현재 활성 구독 수
            active_codes: 활성 구독 종목 목록
            pending_by_priority: {"HIGH": [...], "MEDIUM": [...], "LOW": [...]}
        """
        self._logger.info({
            "action": "summary",
            "active_count": active_count,
            "active_codes": sorted(active_codes),
            "pending_by_priority": pending_by_priority,
        })

    # ── StreamingService 이벤트 ──────────────────────────────────

    def log_connect(self) -> None:
        """WebSocket 연결 성공."""
        self._logger.info({"action": "connect"})

    def log_disconnect(self, reason: str = "") -> None:
        """WebSocket 연결 해제.

        Args:
            reason: 해제 이유 (e.g., "market_closed", "manual", "")
        """
        self._logger.info({"action": "disconnect", "reason": reason})

    # ── WebSocketWatchdogTask 이벤트 ─────────────────────────────

    def log_reconnect(
        self,
        trigger: str,
        codes: list,
        success: int,
        total: int,
    ) -> None:
        """강제 재연결 완료.

        Args:
            trigger: 재연결 원인 ("receive_task_dead" | "data_gap_{N}s")
            codes: 재구독 시도한 종목 목록
            success: 성공한 종목 수
            total: 전체 시도 종목 수
        """
        self._logger.info({
            "action": "reconnect",
            "trigger": trigger,
            "codes": sorted(codes),
            "success": success,
            "total": total,
        })

    def log_restore(self, codes: list, success: int, total: int) -> None:
        """앱 시작 시 구독 상태 복원 완료.

        Args:
            codes: 복원 시도한 종목 목록
            success: 성공한 종목 수
            total: 전체 시도 종목 수
        """
        self._logger.info({
            "action": "restore",
            "codes": sorted(codes),
            "success": success,
            "total": total,
        })

    # ── 프로그램매매 구독 이벤트 (H0STPGM0) ────────────────────

    def log_pt_subscribe(self, code: str, reason: str = "") -> None:
        """프로그램매매 실시간 구독 등록 (H0STPGM0).

        Args:
            code: 종목코드
            reason: 구독 이유 (e.g., "initial", "reconnect", "restore", "user_request")
        """
        self._logger.info({"action": "pt_subscribe", "code": code, "reason": reason})

    def log_pt_unsubscribe(self, code: str, reason: str = "") -> None:
        """프로그램매매 실시간 구독 해제 (H0STPGM0).

        Args:
            code: 종목코드
            reason: 해제 이유 (e.g., "failed", "user_request")
        """
        self._logger.info({"action": "pt_unsubscribe", "code": code, "reason": reason})

    # ── 실시간 체결가 구독 이벤트 (H0STCNT0) ──────────────────

    def log_price_subscribe(self, code: str, reason: str = "") -> None:
        """실시간 현재 체결가 구독 등록 (H0STCNT0).

        워치독이 프로그램매매 종목과 함께 체결가도 함께 구독하는 경우 사용.

        Args:
            code: 종목코드
            reason: 구독 이유 (e.g., "initial", "reconnect", "restore")
        """
        self._logger.info({"action": "price_subscribe", "code": code, "reason": reason})

    def log_price_unsubscribe(self, code: str, reason: str = "") -> None:
        """실시간 현재 체결가 구독 해제 (H0STCNT0).

        Args:
            code: 종목코드
            reason: 해제 이유 (e.g., "failed", "user_request")
        """
        self._logger.info({"action": "price_unsubscribe", "code": code, "reason": reason})


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


class StrategyInfoFilter(logging.Filter):
    """
    전략 로거(strategy.*)의 로그는 INFO 레벨 이상만 통과시키는 필터.
    통합 로그(debug.log)에 전략의 과도한 DEBUG 로그가 쌓이는 것을 방지함.
    """
    def filter(self, record):
        if record.name.startswith("strategy."):
            return record.levelno >= logging.INFO
        return True

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

        # 전략 로그 필터 생성 (debug.log 용량 관리용)
        strategy_filter = StrategyInfoFilter()

        # 기존 로깅 핸들러 제거 및 urllib3 로거 레벨 설정 (중복 로깅 방지)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        # 루트 로거에 통합 로그 핸들러 연결 (전략 로거 등 전파된 로그 수집)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
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
        logger.addHandler(file_handler)

        return logger

    def info(self, message):
        self.operational_logger.info(message, stacklevel=2)
        self.debug_logger.info(message, stacklevel=2)

    def debug(self, message):
        self.debug_logger.debug(message, stacklevel=2)

    def warning(self, message, exc_info=False):
        self.operational_logger.warning(message, exc_info=exc_info, stacklevel=2)
        self.debug_logger.warning(message, exc_info=exc_info, stacklevel=2)

    def error(self, message, exc_info=False):
        self.operational_logger.error(message, exc_info=exc_info, stacklevel=2)
        self.debug_logger.error(message, exc_info=exc_info, stacklevel=2)

    def critical(self, message, exc_info=False):
        self.operational_logger.critical(message, exc_info=exc_info, stacklevel=2)
        self.debug_logger.critical(message, exc_info=exc_info, stacklevel=2)

    def exception(self, message):
        """
        예외 정보를 포함하여 ERROR 레벨로 로그를 남깁니다.
        주로 except 블록 안에서 사용합니다.
        """
        self.error(message, exc_info=True)