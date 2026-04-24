import logging

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
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "subscribe",
            "code": code,
            "categories": categories,
            "active_count": active_count,
        })

    def log_unsubscribe(self, code: str, active_count: int) -> None:
        """통합 현재가 구독 해제.

        Args:
            code: 종목코드
            active_count: 구독 해제 후 총 활성 구독 수
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "summary",
            "active_count": active_count,
            "active_codes": sorted(active_codes),
            "pending_by_priority": pending_by_priority,
        })

    # ── StreamingService 이벤트 ──────────────────────────────────

    def log_connect(self) -> None:
        """WebSocket 연결 성공."""
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "connect"})

    def log_disconnect(self, reason: str = "") -> None:
        """WebSocket 연결 해제.

        Args:
            reason: 해제 이유 (e.g., "market_closed", "manual", "")
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "pt_subscribe", "code": code, "reason": reason})

    def log_pt_unsubscribe(self, code: str, reason: str = "") -> None:
        """프로그램매매 실시간 구독 해제 (H0STPGM0).

        Args:
            code: 종목코드
            reason: 해제 이유 (e.g., "failed", "user_request")
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "pt_unsubscribe", "code": code, "reason": reason})

    # ── 실시간 체결가 구독 이벤트 (H0STCNT0) ──────────────────

    def log_price_subscribe(self, code: str, reason: str = "") -> None:
        """실시간 현재 체결가 구독 등록 (H0STCNT0).

        워치독이 프로그램매매 종목과 함께 체결가도 함께 구독하는 경우 사용.

        Args:
            code: 종목코드
            reason: 구독 이유 (e.g., "initial", "reconnect", "restore")
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "price_subscribe", "code": code, "reason": reason})

    def log_price_unsubscribe(self, code: str, reason: str = "") -> None:
        """실시간 현재 체결가 구독 해제 (H0STCNT0).

        Args:
            code: 종목코드
            reason: 해제 이유 (e.g., "failed", "user_request")
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "price_unsubscribe", "code": code, "reason": reason})

    # ── WebSocket 연결 이벤트 (KoreaInvestWebSocketAPI) ────────────

    def log_connection_lost(self, reason: str, retry_count: int = 0) -> None:
        """예상치 못한 WebSocket 연결 끊김.

        Args:
            reason: 끊김 원인 (e.g., "no close frame", "timeout", "ConnectionClosedError")
            retry_count: 현재까지의 조기 재시도 횟수
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
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
        if not self._logger.isEnabledFor(logging.WARNING):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.ERROR):
            return
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
        if not self._logger.isEnabledFor(logging.ERROR):
            return
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
        price_data_gap_sec: float | None = None,
    ) -> None:
        """워치독 주기 체크 결과 스냅샷.

        Args:
            receive_alive: WebSocket 수신 태스크 생존 여부
            data_gap_sec: 마지막 데이터 수신 이후 경과 시간 (초). 미수신 시 0.0
            market_open: 현재 장 운영 중 여부
            subscribed_count: PT desired 구독 종목 수
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "watchdog_check",
            "receive_alive": receive_alive,
            "data_gap_sec": round(data_gap_sec, 1),
            "price_data_gap_sec": round(price_data_gap_sec, 1) if price_data_gap_sec is not None else None,
            "market_open": market_open,
            "subscribed_count": subscribed_count,
        })

    def log_subscription_recovery_start(self, total: int, codes: list) -> None:
        """구독 복구 시작.

        Args:
            total: 복구 시도 종목 수
            codes: 복구 대상 종목 목록
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
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
        if not self._logger.isEnabledFor(logging.INFO):
            return
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

    # ── WebSocketWatchdogTask 라이프사이클 이벤트 ──────────────────

    def log_watchdog_start(self, task_count: int) -> None:
        """워치독 태스크 시작.

        Args:
            task_count: 생성된 내부 asyncio 태스크 수
        """
        self._logger.info({"action": "watchdog_start", "task_count": task_count})

    def log_watchdog_stop_start(self, task_count: int) -> None:
        """워치독 태스크 종료 시작.

        Args:
            task_count: 취소할 내부 asyncio 태스크 수
        """
        self._logger.info({"action": "watchdog_stop_start", "task_count": task_count})

    def log_watchdog_stop_done(self) -> None:
        """워치독 태스크 종료 완료."""
        self._logger.info({"action": "watchdog_stop_done"})

    def log_watchdog_suspend(self) -> None:
        """워치독 태스크 일시 중지."""
        self._logger.info({"action": "watchdog_suspend"})

    def log_watchdog_resume(self) -> None:
        """워치독 태스크 재개."""
        self._logger.info({"action": "watchdog_resume"})

    # ── WebSocketWatchdogTask 감시 루프 이벤트 ────────────────────

    def log_market_closed_disconnect(self) -> None:
        """장 마감 감지 → WebSocket 연결 종료."""
        self._logger.info({"action": "market_closed_disconnect"})

    def log_market_open_connect(self) -> None:
        """장 시작 감지 → 신규 WebSocket 연결 수립 예정."""
        self._logger.info({"action": "market_open_connect"})

    def log_receive_task_dead(self) -> None:
        """WebSocket 수신 태스크 종료 감지 (비의도적)."""
        self._logger.warning({"action": "receive_task_dead"})

    def log_pt_data_gap(self, data_gap_sec: float, threshold_sec: int) -> None:
        """PT 데이터 수신 갭이 임계값을 초과함.

        Args:
            data_gap_sec: 마지막 PT 데이터 수신 이후 경과 시간 (초)
            threshold_sec: 재연결을 트리거하는 임계값 (초)
        """
        self._logger.warning({
            "action": "pt_data_gap",
            "data_gap_sec": round(data_gap_sec, 1),
            "threshold_sec": threshold_sec,
        })

    def log_price_data_gap(self, data_gap_sec: float, threshold_sec: int) -> None:
        """체결가 데이터 수신 갭이 임계값을 초과함."""
        self._logger.warning({
            "action": "price_data_gap",
            "data_gap_sec": round(data_gap_sec, 1),
            "threshold_sec": threshold_sec,
        })

    def log_stale_price_codes(self, codes: list[str]) -> None:
        """일부 체결가 구독 종목만 stale 상태임."""
        if not codes:
            return
        self._logger.warning({
            "action": "stale_price_codes",
            "codes": sorted(codes),
        })

    def log_missing_reason(self, code: str, reason: str) -> None:
        """체결가 누락 원인을 구조화 로그로 기록한다."""
        self._logger.warning({
            "action": "missing_reason",
            "code": code,
            "reason": reason,
        })

    def log_watchdog_error(self, message: str) -> None:
        """워치독 루프 내 예외 발생.

        Args:
            message: 예외 메시지
        """
        if not self._logger.isEnabledFor(logging.ERROR):
            return
        self._logger.error({"action": "watchdog_error", "message": message})

    # ── WebSocketWatchdogTask 복원 이벤트 ────────────────────────

    def log_pt_restore_connect_failed(self, code: str) -> None:
        """복원 중 특정 PT 종목의 WebSocket 연결 실패.

        Args:
            code: 연결 실패한 종목코드
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        self._logger.warning({"action": "pt_restore_connect_failed", "code": code})

    def log_pt_restore_error(self, code: str, error: str) -> None:
        """복원 중 특정 PT 종목에서 예외 발생.

        Args:
            code: 오류가 발생한 종목코드
            error: 예외 메시지
        """
        if not self._logger.isEnabledFor(logging.ERROR):
            return
        self._logger.error({"action": "pt_restore_error", "code": code, "error": error})

    def log_pt_restore_failed_pending(self, codes: list) -> None:
        """복원 실패한 PT 종목들을 재시도 대기 상태로 기록 (desired는 유지).

        Args:
            codes: 재시도 대기 종목코드 목록
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        self._logger.warning({"action": "pt_restore_failed_pending", "codes": sorted(codes)})

    def log_price_restore_start(self, desired_count: int) -> None:
        """H0UNCNT0(실시간 체결가) 구독 복원 시작.

        Args:
            desired_count: 복원 대상 종목 수
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "price_restore_start", "desired_count": desired_count})

    def log_price_restore_done(self, active_count: int) -> None:
        """H0UNCNT0(실시간 체결가) 구독 복원 완료.

        Args:
            active_count: 복원 후 활성 구독 수
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "price_restore_done", "active_count": active_count})

    # ── WebSocketWatchdogTask 강제 재연결 이벤트 ─────────────────

    def log_force_reconnect_start(self, trigger: str, pt_codes: list) -> None:
        """강제 재연결 시작.

        Args:
            trigger: 재연결 원인
            pt_codes: 재구독 대상 PT 종목 목록
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "force_reconnect_start",
            "trigger": trigger,
            "pt_codes": sorted(pt_codes),
        })

    def log_force_reconnect_disconnect_error(self, error: str) -> None:
        """강제 재연결 전 기존 연결 종료 중 오류 (무시하고 계속).

        Args:
            error: 예외 메시지
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        self._logger.warning({"action": "force_reconnect_disconnect_error", "error": error})

    def log_force_reconnect_done(self, trigger: str) -> None:
        """강제 재연결 완료.

        Args:
            trigger: 재연결 원인
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "force_reconnect_done", "trigger": trigger})

    def log_subscribe_pending(self, code: str, message: str) -> None:
        """구독 보류 이벤트.

        Args:
            code: 종목코드
            message: 로그 메시지 (예: "장 외 시간 — 구독 보류 005930")
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({"action": "subscribe_pending", "code": code, "message": message})
