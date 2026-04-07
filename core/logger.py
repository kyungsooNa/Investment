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

    # ── WebSocket 연결 이벤트 (KoreaInvestWebSocketAPI) ────────────

    def log_connection_lost(self, reason: str, retry_count: int = 0) -> None:
        """예상치 못한 WebSocket 연결 끊김.

        Args:
            reason: 끊김 원인 (e.g., "no close frame", "timeout", "ConnectionClosedError")
            retry_count: 현재까지의 재시도 횟수
        """
        self._logger.warning({
            "action": "connection_lost",
            "reason": reason,
            "retry_count": retry_count,
        })

    def log_appkey_collision(self, retry_count: int, delay_sec: float, max_retries: int) -> None:
        """서버의 'ALREADY IN USE appkey' 거부 이벤트.

        서버가 이전 세션을 아직 보유 중이어서 동일 appkey 재사용을 거부할 때 기록.

        Args:
            retry_count: 현재 시도 번호 (1-based)
            delay_sec: 다음 재연결까지 대기 시간 (초)
            max_retries: 최대 재시도 횟수
        """
        self._logger.warning({
            "action": "appkey_collision",
            "retry_count": retry_count,
            "delay_sec": delay_sec,
            "max_retries": max_retries,
        })

    def log_reconnect_attempt(self, attempt_num: int, max_attempts: int, was_collision: bool = False) -> None:
        """WebSocket 재연결 시도 시작.

        Args:
            attempt_num: 현재 시도 번호 (1-based)
            max_attempts: 최대 시도 횟수
            was_collision: True이면 appkey 충돌로 인한 재연결
        """
        self._logger.info({
            "action": "reconnect_attempt",
            "attempt_num": attempt_num,
            "max_attempts": max_attempts,
            "was_collision": was_collision,
        })

    def log_reconnect_success(self, attempt_num: int, max_attempts: int) -> None:
        """WebSocket 재연결 성공.

        Args:
            attempt_num: 현재 시도 번호 (1-based)
            max_attempts: 최대 시도 횟수
        """
        self._logger.info({
            "action": "reconnect_success",
            "attempt_num": attempt_num,
            "max_attempts": max_attempts,
        })

    def log_unsubscribe_failure(self, code: str, message: str) -> None:
        """구독 해지 실패 이벤트.

        Args:
            code: 종목코드
            message: 실패 메시지 (예: "WebSocket 미연결", "브로커 거부", "예외 발생 {e}")
        """
        self._logger.error({
            "action": "unsubscribe_failure",
            "code": code,
            "message": message,
        })

    def log_subscribe_failure(self, code: str, message: str) -> None:
        """구독 등록 실패 이벤트.

        Args:
            code: 종목코드
            message: 실패 메시지 (예: "WebSocket 미연결", "브로커 거부", "예외 발생 {e}")
        """
        self._logger.error({
            "action": "subscribe_failure",
            "code": code,
            "message": message,
        })

    # ── WebSocketWatchdogTask 이벤트 (확장) ─────────────────────────

    def log_watchdog_check(
        self,
        receive_alive: bool,
        data_gap_sec: float,
        market_open: bool,
        subscribed_count: int,
    ) -> None:
        """워치독 주기 체크 결과 스냅샷.

        Args:
            receive_alive: WebSocket 수신 태스크 생존 여부
            data_gap_sec: 마지막 데이터 수신 이후 경과 시간 (초). 미수신 시 0.0
            market_open: 현재 장 운영 중 여부
            subscribed_count: PT desired 구독 종목 수
        """
        self._logger.debug({
            "action": "watchdog_check",
            "receive_alive": receive_alive,
            "data_gap_sec": round(data_gap_sec, 1),
            "market_open": market_open,
            "subscribed_count": subscribed_count,
        })

    def log_subscription_recovery_start(self, total: int, codes: list) -> None:
        """구독 복구 시작.

        Args:
            total: 복구 시도 종목 수
            codes: 복구 대상 종목 목록
        """
        self._logger.info({
            "action": "subscription_recovery_start",
            "total": total,
            "codes": sorted(codes),
        })

    def log_subscription_recovery_done(
        self,
        success: int,
        total: int,
        failed_codes: list,
        elapsed_ms: float,
    ) -> None:
        """구독 복구 완료.

        Args:
            success: 복구 성공 종목 수
            total: 전체 시도 종목 수
            failed_codes: 복구 실패 종목 목록
            elapsed_ms: 복구 소요 시간 (밀리초)
        """
        self._logger.info({
            "action": "subscription_recovery_done",
            "success": success,
            "total": total,
            "failed_codes": sorted(failed_codes),
            "elapsed_ms": round(elapsed_ms, 1),
        })

    def log_clear_active_state(self, message: str) -> None:
        """active 상태 초기화 이벤트.

        Args:
            message: 로그 메시지 (예: "SubscriptionPolicy: active 상태 초기화 10개 클리어")
        """
        self._logger.info({"action": "clear_active_state", "message": message})

    def log_add_subscription_rejection(self, code: str, message: str) -> None:
        """구독 추가 거절 이벤트.

        Args:
            code: 종목코드
            message: 로그 메시지 (예: "웹소켓 한도 초과: 005930 프로그램 매매 구독 거절")
        """
        self._logger.warning({"action": "add_subscription_rejection", "code": code, "message": message})
    
    def log_dropped_subscriptions(self, message: str) -> None:
        """구독 한도 초과로 대기 상태에 놓인 종목 이벤트.

        Args:
            message: 로그 메시지 (예: "SubscriptionPolicy: 웹소켓 구독 한도 초과 — 5개 종목이 대기 상태 (active_pt=100, active_price=50, requested=160, max_slots=150)")
        """
        self._logger.warning({"action": "dropped_subscriptions", "message": message})

    def log_subscribe_pending(self, code: str, message: str) -> None:
        """구독 보류 이벤트.

        Args:
            code: 종목코드
            message: 로그 메시지 (예: "장 외 시간 — 구독 보류 005930")
        """
        self._logger.info({"action": "subscribe_pending", "code": code, "message": message})

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


class CacheEventLogger:
    """
    현재가·OHLCV 캐시 동작을 JSON으로 기록하는 전용 로거.

    logs/cache/{timestamp}_cache.log.json 에 기록한다.

    --- 로그 종류 ---

    [현재가 캐시 — StockPriceRepository (LRU)]
      price_set         : API 응답으로 현재가 캐시 등록/갱신 (before/after price, is_new)
      price_update_tick : WebSocket 틱으로 현재가 갱신 (before/after price, volume)
      price_hit         : 캐시 히트 (caller, age_sec, is_streaming)
      price_miss        : 캐시 미스 (caller, reason: "not_found" | "ttl_expired")
      price_evicted     : LRU capacity 초과로 캐시 제거

    [스트리밍 상태 — StockPriceRepository]
      streaming_mark    : 실시간 스트리밍 등록 (streaming_count)
      streaming_unmark  : 실시간 스트리밍 해제 (streaming_count)

    [OHLCV 캐시 — StockOhlcvRepository (LFU)]
      ohlcv_loaded      : DB에서 OHLCV 로드 후 캐시 등록 (caller, ohlcv_count, latest_date)
      ohlcv_hit         : 캐시 히트 (caller, ohlcv_count, has_today_candle)
      ohlcv_miss        : 캐시 미스 (caller)
      ohlcv_evicted     : LFU capacity 초과로 캐시 제거 (freq, ohlcv_count)
      ohlcv_invalidated : upsert 후 캐시 무효화
      ohlcv_upsert      : OHLCV upsert 배치 완료 (record_count, code_count, invalidated_codes)
      today_candle      : 당일 캔들 갱신 (before/after price, high, low, is_new_candle)

    [통합 통계]
      cache_stats       : 현재가+OHLCV 캐시 hit/miss 통계 스냅샷
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    # ── 현재가 캐시 이벤트 ───────────────────────────────────────────

    def log_price_set(
        self,
        code: str,
        caller: str,
        before_price: str,
        after_price: str,
        is_new: bool,
    ) -> None:
        """API 응답으로 현재가 캐시 등록 또는 갱신.

        Args:
            code: 종목코드
            caller: 호출 출처 (e.g., "market_data_service", "streaming")
            before_price: 갱신 전 stck_prpr (캐시 미존재 시 None)
            after_price: 갱신 후 stck_prpr
            is_new: True이면 캐시에 처음 등록 (신규 종목)
        """
        self._logger.info({
            "action": "price_set",
            "code": code,
            "caller": caller,
            "before_price": before_price,
            "after_price": after_price,
            "is_new": is_new,
        })

    def log_price_update_tick(
        self,
        code: str,
        before_price: str,
        after_price: str,
        volume: int,
    ) -> None:
        """WebSocket 틱 데이터로 현재가 갱신. 가격 변동 시에만 기록.

        Args:
            code: 종목코드
            before_price: 갱신 전 stck_prpr
            after_price: 갱신 후 stck_prpr
            volume: 누적 거래량
        """
        self._logger.debug({
            "action": "price_update_tick",
            "code": code,
            "before_price": before_price,
            "after_price": after_price,
            "volume": volume,
        })

    def log_price_hit(
        self,
        code: str,
        caller: str,
        age_sec: float,
        is_streaming: bool,
    ) -> None:
        """현재가 캐시 히트.

        Args:
            code: 종목코드
            caller: 호출 출처
            age_sec: 캐시 데이터 경과 시간 (초)
            is_streaming: 실시간 스트리밍 중 여부 (TTL 무제한)
        """
        self._logger.debug({
            "action": "price_hit",
            "code": code,
            "caller": caller,
            "age_sec": round(age_sec, 2),
            "is_streaming": is_streaming,
        })

    def log_price_miss(self, code: str, caller: str, reason: str) -> None:
        """현재가 캐시 미스.

        Args:
            code: 종목코드
            caller: 호출 출처
            reason: "not_found" | "ttl_expired"
        """
        self._logger.debug({
            "action": "price_miss",
            "code": code,
            "caller": caller,
            "reason": reason,
        })

    def log_price_evicted(self, code: str, capacity: int) -> None:
        """LRU 용량 초과로 현재가 캐시에서 제거.

        Args:
            code: 제거된 종목코드
            capacity: 캐시 최대 용량
        """
        self._logger.warning({
            "action": "price_evicted",
            "code": code,
            "capacity": capacity,
        })

    # ── 스트리밍 상태 이벤트 ─────────────────────────────────────────

    def log_streaming_mark(self, code: str, streaming_count: int) -> None:
        """실시간 스트리밍 등록 (TTL 무제한 전환).

        Args:
            code: 종목코드
            streaming_count: 등록 후 총 스트리밍 종목 수
        """
        self._logger.info({
            "action": "streaming_mark",
            "code": code,
            "streaming_count": streaming_count,
        })

    def log_streaming_unmark(self, code: str, streaming_count: int) -> None:
        """실시간 스트리밍 해제 (TTL 정상 적용 복귀).

        Args:
            code: 종목코드
            streaming_count: 해제 후 총 스트리밍 종목 수
        """
        self._logger.info({
            "action": "streaming_unmark",
            "code": code,
            "streaming_count": streaming_count,
        })

    # ── OHLCV 캐시 이벤트 ───────────────────────────────────────────

    def log_ohlcv_loaded(
        self,
        code: str,
        caller: str,
        ohlcv_count: int,
        latest_date: str,
    ) -> None:
        """DB에서 OHLCV 데이터를 읽어 캐시에 등록.

        Args:
            code: 종목코드
            caller: 호출 출처
            ohlcv_count: 적재된 OHLCV 일수
            latest_date: 가장 최근 OHLCV 날짜 (데이터 신선도 확인)
        """
        self._logger.info({
            "action": "ohlcv_loaded",
            "code": code,
            "caller": caller,
            "ohlcv_count": ohlcv_count,
            "latest_date": latest_date,
        })

    def log_ohlcv_hit(
        self,
        code: str,
        caller: str,
        ohlcv_count: int,
        has_today_candle: bool,
    ) -> None:
        """OHLCV 캐시 히트.

        Args:
            code: 종목코드
            caller: 호출 출처
            ohlcv_count: 캐시에 있는 총 OHLCV 일수 (historical + today 포함)
            has_today_candle: 당일 캔들 존재 여부
        """
        self._logger.debug({
            "action": "ohlcv_hit",
            "code": code,
            "caller": caller,
            "ohlcv_count": ohlcv_count,
            "has_today_candle": has_today_candle,
        })

    def log_ohlcv_miss(self, code: str, caller: str) -> None:
        """OHLCV 캐시 미스 (DB 조회 필요).

        Args:
            code: 종목코드
            caller: 호출 출처
        """
        self._logger.debug({
            "action": "ohlcv_miss",
            "code": code,
            "caller": caller,
        })

    def log_ohlcv_evicted(self, code: str, freq: int, ohlcv_count: int, capacity: int) -> None:
        """LFU 용량 초과로 OHLCV 캐시에서 제거.

        Args:
            code: 제거된 종목코드
            freq: 제거 시점까지의 누적 접근 횟수 (낮을수록 자주 안 쓰인 종목)
            ohlcv_count: 제거된 종목의 OHLCV 일수
            capacity: 캐시 최대 용량
        """
        self._logger.warning({
            "action": "ohlcv_evicted",
            "code": code,
            "freq": freq,
            "ohlcv_count": ohlcv_count,
            "capacity": capacity,
        })

    def log_ohlcv_invalidated(self, code: str) -> None:
        """upsert 이후 해당 종목 OHLCV 캐시 무효화 (다음 조회 시 DB에서 재로드).

        Args:
            code: 무효화된 종목코드
        """
        self._logger.info({
            "action": "ohlcv_invalidated",
            "code": code,
        })

    def log_ohlcv_upsert(
        self,
        record_count: int,
        code_count: int,
        invalidated_codes: list,
    ) -> None:
        """OHLCV upsert 배치 완료 및 캐시 무효화 요약.

        Args:
            record_count: upsert된 총 레코드 수
            code_count: 영향 받은 고유 종목 수
            invalidated_codes: 캐시 무효화된 종목코드 목록
        """
        self._logger.info({
            "action": "ohlcv_upsert",
            "record_count": record_count,
            "code_count": code_count,
            "invalidated_codes": sorted(invalidated_codes),
        })

    def log_today_candle(
        self,
        code: str,
        before_price,
        after_price: float,
        high: float,
        low: float,
        is_new_candle: bool,
    ) -> None:
        """WebSocket 틱으로 당일 캔들 갱신.

        Args:
            code: 종목코드
            before_price: 갱신 전 close 가격 (캔들 없으면 None)
            after_price: 갱신 후 close 가격
            high: 갱신 후 고가
            low: 갱신 후 저가
            is_new_candle: True이면 ohlcv_today 신규 생성 (기존 historical[-1] 갱신이 아님)
        """
        self._logger.debug({
            "action": "today_candle",
            "code": code,
            "before_price": before_price,
            "after_price": after_price,
            "high": high,
            "low": low,
            "is_new_candle": is_new_candle,
        })

    # ── 통합 통계 ─────────────────────────────────────────────────────

    def log_stats(self, price_stats: dict, ohlcv_stats: dict) -> None:
        """현재가 + OHLCV 캐시 통합 hit/miss 통계 스냅샷.

        Args:
            price_stats: StockPriceRepository.get_cache_stats() 결과
            ohlcv_stats: StockOhlcvRepository.get_cache_stats() 결과
        """
        total_hits = price_stats.get("hits", 0) + ohlcv_stats.get("hits", 0)
        total_misses = price_stats.get("misses", 0) + ohlcv_stats.get("misses", 0)
        total = total_hits + total_misses
        self._logger.info({
            "action": "cache_stats",
            "price": {
                "hits": price_stats.get("hits", 0),
                "misses": price_stats.get("misses", 0),
                "hit_rate": price_stats.get("hit_rate", 0.0),
                "current_size": price_stats.get("current_size", 0),
            },
            "ohlcv": {
                "hits": ohlcv_stats.get("hits", 0),
                "misses": ohlcv_stats.get("misses", 0),
                "hit_rate": ohlcv_stats.get("hit_rate", 0.0),
                "current_size": ohlcv_stats.get("current_size", 0),
            },
            "combined": {
                "hits": total_hits,
                "misses": total_misses,
                "hit_rate": round(total_hits / total * 100, 2) if total > 0 else 0.0,
            },
        })


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