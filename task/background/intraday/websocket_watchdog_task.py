# task/background/intraday/websocket_watchdog_task.py
"""
프로그램매매 WebSocket 연결 감시 및 자동 복원 태스크.
WebSocket 수신 태스크 상태를 주기적으로 감시하고,
데이터 수신이 끊기면 재연결한다.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from core.performance_profiler import PerformanceProfiler
from services.notification_service import NotificationService
from common.operator_alert_types import AlertSource

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from services.program_trading_stream_service import ProgramTradingStreamService
    from services.market_calendar_service import MarketCalendarService
    from services.price_subscription_service import PriceSubscriptionService
    from services.price_stream_service import PriceStreamService
    from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType
    from core.logger import StreamingEventLogger
    from services.operator_alert_service import OperatorAlertService

class WebSocketWatchdogTask(SchedulableTask):
    """프로그램매매 WebSocket 연결을 감시·복원하는 백그라운드 태스크."""

    # 재구독 시 패킷 간 딜레이 (초) — 증권사 Rate Limit 방지
    SUBSCRIBE_DELAY_SEC = 0.2
    WATCHDOG_INTERVAL_SEC = 60
    PT_DATA_GAP_THRESHOLD_SEC = 300
    PRICE_DATA_GAP_THRESHOLD_SEC = 180
    PRICE_SUBSCRIPTION_GRACE_SEC = 30
    SUBSCRIBED_NO_TICK_REFRESH_COOLDOWN_SEC = 300
    # 무효 refresh가 이 횟수에 도달하면 종목을 격리해 unsub/resub churn을 중단한다.
    QUARANTINE_NO_TICK_REFRESH_THRESHOLD = 3
    RECONNECT_COOLDOWN_SEC = 10.0
    RECONNECT_ALERT_CONFIRMATION_COUNT = 2
    REALTIME_HEALTH_CHECK_END_HOUR = 15
    REALTIME_HEALTH_CHECK_END_MINUTE = 30

    def __init__(
        self,
        streaming_service: Optional["StreamingService"] = None,
        program_trading_stream_service: Optional["ProgramTradingStreamService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        operator_alert_service: Optional["OperatorAlertService"] = None,
        logger=None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
        streaming_stock_repo: Optional["StreamingStockRepo"] = None,
        price_subscription_service: Optional["PriceSubscriptionService"] = None,
        price_stream_service: Optional["PriceStreamService"] = None,
    ):
        self._streaming_service = streaming_service
        self._program_trading_stream_service = program_trading_stream_service
        self.mcs = market_calendar_service
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._ns = notification_service
        self._oas = operator_alert_service
        self._logger = logger or logging.getLogger(__name__)
        self._streaming_logger = streaming_logger
        self._streaming_stock_repo = streaming_stock_repo
        self._price_subscription_service = price_subscription_service
        self._price_stream_service = price_stream_service

        # SchedulableTask 상태
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._market_open: Optional[bool] = None  # 가장 최근 시장 개장 여부 (워치독 루프에서 갱신)
        self._intentionally_disconnected: bool = False  # 장 마감으로 인한 의도적 연결 종료 여부
        self._last_subscribed_no_tick_refresh_ts: Dict[str, float] = {}
        self._no_tick_refresh_counts: Dict[str, int] = {}
        self._quarantined_no_tick_codes: set = set()
        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_started_ts: float = 0.0
        self._reconnect_trigger_counts: Dict[str, int] = {}
        self._pt_no_initial_data_started_ts: Optional[float] = None

    # ── SchedulableTask 인터페이스 구현 ────────────────────────

    @property
    def task_name(self) -> str:
        return "websocket_watchdog"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        """WebSocket 워치독 + 구독 복원 태스크를 시작한다."""
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.IDLE  # 워치독 루프가 장 중 여부 확인 후 RUNNING으로 전환

        # 1. 실시간 데이터 매니저 백그라운드 태스크 (데이터 정리 등)
        if self._program_trading_stream_service:
            self._program_trading_stream_service.start_background_tasks()

        # 2. 이전 구독 상태 자동 복원 (PT + H0UNCNT0 통합 복원)
        self._tasks.append(
            asyncio.create_task(self._restore_all_subscriptions())
        )

        # 3. 프로그램매매 연결 상태 워치독
        self._tasks.append(
            asyncio.create_task(self._streaming_watchdog())
        )

        if self._streaming_logger:
            self._streaming_logger.log_watchdog_start(len(self._tasks))

    async def stop(self) -> None:
        """모든 워치독 태스크를 취소하고 정리한다."""
        if self._streaming_logger:
            self._streaming_logger.log_watchdog_stop_start(len(self._tasks))

        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        # 실시간 데이터 매니저 종료
        if self._program_trading_stream_service:
            await self._program_trading_stream_service.shutdown()

        self._state = TaskState.STOPPED
        if self._streaming_logger:
            self._streaming_logger.log_watchdog_stop_done()

    async def suspend(self) -> None:
        """워치독을 일시 중지한다."""
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED
            if self._streaming_logger:
                self._streaming_logger.log_watchdog_suspend()

    async def resume(self) -> None:
        """워치독을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING
            if self._streaming_logger:
                self._streaming_logger.log_watchdog_resume()

    # ── 프로그램매매 워치독 / 복원 / 재연결 ──────────────────────

    async def _streaming_watchdog(self) -> None:
        """WebSocket 연결 상태를 주기적으로 감시하고, 데이터 수신이 끊기면 재연결.

        PT와 실시간 체결가(H0UNCNT0) 구독 여부를 모두 확인하여,
        둘 중 하나라도 활성 구독이 있으면 감시를 수행한다.
        """
        while True:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL_SEC)

                # suspend 상태이면 감시 스킵
                if self._state == TaskState.SUSPENDED:
                    continue

                # PT 구독 종목 확인 — StreamingStockRepo가 SSOT
                from repositories.streaming_stock_repo import StreamingType
                pt_codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) \
                    if self._streaming_stock_repo else []
                raw_price_codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.UNIFIED_PRICE)) \
                    if self._streaming_stock_repo else []
                price_codes = sorted(set(raw_price_codes) | set(pt_codes))
                active_price_codes = set(self._streaming_stock_repo.get_active(StreamingType.UNIFIED_PRICE)) \
                    if self._streaming_stock_repo else set()
                active_pt_codes = set(self._streaming_stock_repo.get_active(StreamingType.PROGRAM_TRADING)) \
                    if self._streaming_stock_repo else set()
                active_price_like_codes = active_price_codes | active_pt_codes

                # 실시간 체결가(H0UNCNT0) 구독 여부 확인
                has_price_subs = bool(price_codes)

                # PT 구독도 없고 실시간 체결가 구독도 없으면 감시 불필요
                if not pt_codes and not has_price_subs:
                    continue

                market_is_open = bool(self.mcs and await self.mcs.is_market_open_now())
                self._market_open = market_is_open

                # 장 개폐에 따른 state 전환 (SUSPENDED/STOPPED는 건드리지 않음)
                if market_is_open and self._state == TaskState.IDLE:
                    self._state = TaskState.RUNNING
                elif not market_is_open and self._state == TaskState.RUNNING:
                    self._state = TaskState.IDLE

                if not market_is_open:
                    # 장 마감 시간이면 연결을 명시적으로 종료하여 리소스 정리
                    if self._streaming_service and self._streaming_service.broker.is_websocket_receive_alive():
                        if self._streaming_logger:
                            self._streaming_logger.log_market_closed_disconnect()
                        await self._streaming_service.disconnect_websocket()
                        self._intentionally_disconnected = True
                    continue

                if not self._is_realtime_health_check_window():
                    self._pt_no_initial_data_started_ts = None
                    self._reconnect_trigger_counts.clear()
                    continue

                # 조건 1: 수신 태스크가 죽었는지 확인 (PT/체결가 공통)
                receive_alive = (
                    self._streaming_service is not None
                    and self._streaming_service.broker.is_websocket_receive_alive()
                )

                # 조건 2: PT 데이터 수신 갭 확인 (PT 종목이 있을 때만 — last_data_ts가 PT 기준)
                data_gap = 0.0
                pt_no_initial_data_gap = None
                if pt_codes and self._program_trading_stream_service:
                    last_ts = self._program_trading_stream_service.last_data_ts
                    if last_ts > 0:
                        data_gap = time.time() - last_ts
                        self._pt_no_initial_data_started_ts = None
                    elif active_pt_codes:
                        now_ts = time.time()
                        if self._pt_no_initial_data_started_ts is None:
                            self._pt_no_initial_data_started_ts = now_ts
                        pt_no_initial_data_gap = now_ts - self._pt_no_initial_data_started_ts
                        data_gap = pt_no_initial_data_gap
                    else:
                        self._pt_no_initial_data_started_ts = None
                else:
                    self._pt_no_initial_data_started_ts = None

                price_gap = None
                stale_price_codes = []
                if has_price_subs and self._price_stream_service:
                    last_any_tick_ts = self._price_stream_service.get_last_any_tick_ts()
                    if last_any_tick_ts > 0:
                        price_gap = time.time() - last_any_tick_ts
                    stale_price_codes = self._price_stream_service.get_stale_codes(
                        self.PRICE_DATA_GAP_THRESHOLD_SEC,
                        codes=price_codes,
                    )

                if self._streaming_logger:
                    self._streaming_logger.log_watchdog_check(
                        receive_alive=receive_alive,
                        data_gap_sec=data_gap,
                        price_data_gap_sec=price_gap,
                        market_open=market_is_open,
                        subscribed_count=len(set(pt_codes) | set(price_codes)),
                    )
                    if stale_price_codes:
                        self._streaming_logger.log_stale_price_codes(stale_price_codes)

                if has_price_subs and self._price_stream_service:
                    not_subscribed_codes = [
                        code for code in price_codes
                        if code not in active_price_like_codes
                        and self._price_stream_service.get_subscription_age(code) > self.PRICE_SUBSCRIPTION_GRACE_SEC
                    ]
                    if not_subscribed_codes:
                        for code in not_subscribed_codes:
                            if self._streaming_logger:
                                self._streaming_logger.log_missing_reason(code, "not_subscribed")
                        if self._price_subscription_service:
                            await self._price_subscription_service._rebalance()

                    # KIS가 결국 프레임을 보내기 시작한 격리 종목은 격리를 해제한다.
                    self._release_recovered_no_tick_codes()

                    subscribed_no_tick_codes = [
                        code for code in stale_price_codes
                        if code in active_price_like_codes
                        and self._price_stream_service.get_last_tick_ts(code) <= 0
                    ]
                    if receive_alive and subscribed_no_tick_codes:
                        refreshable_no_tick_codes = []
                        for code in subscribed_no_tick_codes:
                            if code in self._quarantined_no_tick_codes:
                                # 격리 종목은 refresh churn을 일으키지 않고 가시화만 한다.
                                if self._streaming_logger:
                                    self._streaming_logger.log_missing_reason(code, "quarantined_no_tick")
                                continue
                            if self._streaming_logger:
                                self._streaming_logger.log_missing_reason(code, "subscribed_no_tick")
                            refreshable_no_tick_codes.append(code)
                        if refreshable_no_tick_codes:
                            await self._refresh_subscribed_no_tick_codes(refreshable_no_tick_codes)

                reconnect_trigger = None
                if not receive_alive:
                    # 연결이 죽었으면 PT/체결가 구분 없이 재연결
                    if self._intentionally_disconnected:
                        if self._streaming_logger:
                            self._streaming_logger.log_market_open_connect()
                        reconnect_trigger = "market_open"
                    else:
                        if self._streaming_logger:
                            self._streaming_logger.log_receive_task_dead()
                        reconnect_trigger = "receive_task_dead"
                elif (
                    pt_codes
                    and pt_no_initial_data_gap is not None
                    and pt_no_initial_data_gap > self.PT_DATA_GAP_THRESHOLD_SEC
                ):
                    if self._streaming_logger:
                        self._streaming_logger.log_pt_data_gap(
                            pt_no_initial_data_gap,
                            self.PT_DATA_GAP_THRESHOLD_SEC,
                        )
                    reconnect_trigger = f"pt_no_initial_data_{pt_no_initial_data_gap:.0f}s"
                elif pt_codes and data_gap > self.PT_DATA_GAP_THRESHOLD_SEC:
                    # 수신 태스크는 살아있지만 PT 데이터가 임계값 이상 안 오는 경우
                    if self._streaming_logger:
                        self._streaming_logger.log_pt_data_gap(data_gap, self.PT_DATA_GAP_THRESHOLD_SEC)
                    reconnect_trigger = f"pt_data_gap_{data_gap:.0f}s"
                elif has_price_subs and price_gap is not None and price_gap > self.PRICE_DATA_GAP_THRESHOLD_SEC:
                    if self._streaming_logger:
                        self._streaming_logger.log_price_data_gap(price_gap, self.PRICE_DATA_GAP_THRESHOLD_SEC)
                    reconnect_trigger = f"price_data_gap_{price_gap:.0f}s"

                if reconnect_trigger:
                    self._intentionally_disconnected = False
                    should_report_reconnect = self._should_report_reconnect_trigger(reconnect_trigger)
                    if self._oas and reconnect_trigger != "market_open" and should_report_reconnect:
                        await self._oas.report(
                            AlertSource.WEBSOCKET_WATCHDOG, "websocket_watchdog:reconnect",
                            "error", "WebSocket 재연결 트리거",
                            f"trigger={reconnect_trigger}",
                        )
                    await self.force_reconnect(trigger=reconnect_trigger)
                else:
                    pending_pt_codes = [
                        code for code in pt_codes
                        if code not in active_pt_codes
                    ]
                    if pending_pt_codes:
                        for code in pending_pt_codes:
                            if self._streaming_logger:
                                self._streaming_logger.log_missing_reason(code, "pt_not_active")
                        await self._restore_all_subscriptions()
                        continue
                    self._reconnect_trigger_counts.clear()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._streaming_logger:
                    self._streaming_logger.log_watchdog_error(str(e))

    def _should_report_reconnect_trigger(self, trigger: str) -> bool:
        """일시적 흔들림 알림을 줄이기 위해 같은 재연결 원인이 연속 감지될 때만 알린다."""
        if trigger == "market_open":
            return False

        key = self._normalize_reconnect_trigger(trigger)
        count = self._reconnect_trigger_counts.get(key, 0) + 1
        self._reconnect_trigger_counts = {key: count}
        return count >= self.RECONNECT_ALERT_CONFIRMATION_COUNT

    @staticmethod
    def _normalize_reconnect_trigger(trigger: str) -> str:
        if trigger.startswith("pt_data_gap_"):
            return "pt_data_gap"
        if trigger.startswith("price_data_gap_"):
            return "price_data_gap"
        return trigger

    def _is_realtime_health_check_window(self) -> bool:
        """정규장 실시간 tick이 기대되는 시간대에만 데이터 gap 감시를 수행한다."""
        now = time.localtime()
        return (
            now.tm_hour,
            now.tm_min,
        ) < (
            self.REALTIME_HEALTH_CHECK_END_HOUR,
            self.REALTIME_HEALTH_CHECK_END_MINUTE,
        )

    def _release_recovered_no_tick_codes(self) -> None:
        """격리된 종목이 다시 틱을 받으면 격리를 해제한다."""
        if not self._price_stream_service:
            return
        for code in list(self._quarantined_no_tick_codes):
            if self._price_stream_service.get_last_tick_ts(code) > 0:
                self._quarantined_no_tick_codes.discard(code)
                self._no_tick_refresh_counts.pop(code, None)
                if self._streaming_logger:
                    self._streaming_logger.log_missing_reason(code, "no_tick_recovered")

    async def _refresh_subscribed_no_tick_codes(self, codes: List[str]) -> None:
        """첫 틱이 오지 않는 가격 구독을 개별 재요청한다."""
        if not self._streaming_service:
            return

        now_ts = time.time()
        for code in codes:
            if code in self._quarantined_no_tick_codes:
                # 격리 종목은 unsub/resub churn을 일으키지 않는다.
                continue

            last_refresh_ts = self._last_subscribed_no_tick_refresh_ts.get(code, 0.0)
            if (now_ts - last_refresh_ts) < self.SUBSCRIBED_NO_TICK_REFRESH_COOLDOWN_SEC:
                continue

            await self._streaming_service.unsubscribe_unified_price(code)
            if self._streaming_logger:
                self._streaming_logger.log_price_unsubscribe(code, reason="subscribed_no_tick_refresh")

            await asyncio.sleep(self.SUBSCRIBE_DELAY_SEC)

            success = await self._streaming_service.subscribe_unified_price(code)
            ack_confirmed = True
            if success:
                wait_ack = getattr(self._streaming_service, "wait_unified_price_ack", None)
                if callable(wait_ack):
                    ack_confirmed = bool(await wait_ack(code))
            if success and ack_confirmed and self._streaming_logger:
                self._streaming_logger.log_price_subscribe(code, reason="subscribed_no_tick_refresh")
            elif self._streaming_logger:
                reason = "ACK 미확정" if success else "구독 요청 실패"
                self._streaming_logger.log_subscribe_failure(
                    code,
                    f"subscribed_no_tick_refresh {reason}",
                )

            self._last_subscribed_no_tick_refresh_ts[code] = time.time()

            # 무효 refresh 누적 추적 — 임계값 도달 시 격리해 churn 중단.
            self._no_tick_refresh_counts[code] = self._no_tick_refresh_counts.get(code, 0) + 1
            if self._no_tick_refresh_counts[code] >= self.QUARANTINE_NO_TICK_REFRESH_THRESHOLD:
                self._quarantined_no_tick_codes.add(code)
                if self._streaming_logger:
                    self._streaming_logger.log_missing_reason(code, "quarantined_no_tick")

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현).

        Watchdog 태스크는 배치 진행률이 없으므로 연결 상태 정보를 반환한다.
        """
        subscribed_pt = 0
        subscribed_price = 0
        if self._streaming_stock_repo:
            from repositories.streaming_stock_repo import StreamingType
            subscribed_pt = len(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING))
            subscribed_price = len(self._streaming_stock_repo.get_desired(StreamingType.UNIFIED_PRICE))

        last_ts = 0.0
        data_gap = None
        price_gap = None
        if self._program_trading_stream_service:
            last_ts = getattr(self._program_trading_stream_service, "last_data_ts", 0.0)
            if last_ts > 0:
                data_gap = round(time.time() - last_ts, 1)

        if self._price_stream_service:
            last_price_ts = self._price_stream_service.get_last_any_tick_ts()
            if last_price_ts > 0:
                price_gap = round(time.time() - last_price_ts, 1)

        return {
            "running": self._state == TaskState.RUNNING,
            "subscribed_codes": subscribed_pt + subscribed_price,
            "subscribed_pt_codes": subscribed_pt,
            "subscribed_price_codes": subscribed_price,
            "data_gap_sec": data_gap,
            "price_data_gap_sec": price_gap,
            "market_open": self._market_open,
        }

    async def _restore_all_subscriptions(self) -> None:
        """
        앱 시작 또는 재연결 직후 모든 구독(PT + H0UNCNT0)을 복원한다.

        핵심 순서:
          1. PT active 상태 초기화 (브로커 연결 리셋 → 내부 상태도 리셋)
          2. PT + H0STCNT0 재구독
          3. H0UNCNT0 active 상태 초기화 후 _rebalance()로 재구독
             (단순 _rebalance() 호출만 하면 _active_codes에 이전 상태가 남아
              "이미 구독됨"으로 판단해 브로커에 subscribe를 보내지 않는 버그 방지)
        """
        # 장 마감 중에는 WebSocket 연결 불필요 — 워치독 루프가 장 개장 시 재연결 처리
        market_is_open = bool(self.mcs and await self.mcs.is_market_open_now())
        if not market_is_open:
            self._logger.info("[WebSocketWatchdog] 장 마감 중 — 구독 복원 생략 (워치독이 장 개장 시 재연결)")
            return

        from repositories.streaming_stock_repo import StreamingType

        # ── 1. PT active 상태 초기화 ──────────────────────────────
        if self._streaming_stock_repo:
            await self._streaming_stock_repo.clear_active(StreamingType.PROGRAM_TRADING)

        # ── 2. PT + H0STCNT0 복원 ─────────────────────────────────
        pt_codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) if self._streaming_stock_repo else []

        import time as _time
        _recovery_start = _time.monotonic()

        pt_success = 0
        pt_failed = []
        if pt_codes:
            if self._streaming_logger:
                self._streaming_logger.log_subscription_recovery_start(
                    total=len(pt_codes),
                    codes=pt_codes,
                )
            connected = await self._streaming_service.connect_websocket()
            if not connected:
                if self._streaming_logger:
                    self._streaming_logger.log_pt_restore_connect_failed(f"all({len(pt_codes)})")
                pt_failed = list(pt_codes)
            else:
                for code in pt_codes:
                    try:
                        await self._streaming_service.subscribe_program_trading(code)
                        if self._streaming_logger:
                            self._streaming_logger.log_pt_subscribe(code, reason="restore")
                        await self._streaming_service.subscribe_unified_price(code)
                        if self._streaming_logger:
                            self._streaming_logger.log_price_subscribe(code, reason="restore")
                        if self._streaming_stock_repo:
                            await self._streaming_stock_repo.mark_active(code, StreamingType.PROGRAM_TRADING)
                        pt_success += 1
                        await asyncio.sleep(self.SUBSCRIBE_DELAY_SEC)
                    except Exception as e:
                        if self._streaming_logger:
                            self._streaming_logger.log_pt_restore_error(code, str(e))
                        pt_failed.append(code)

        if pt_failed:
            # desired 유지 — 다음 watchdog tick(60초)에서 재시도
            if self._streaming_logger:
                self._streaming_logger.log_pt_restore_failed_pending(pt_failed)

        if pt_codes:
            if self._streaming_logger:
                self._streaming_logger.log_subscription_recovery_done(
                    success=pt_success,
                    total=len(pt_codes),
                    failed_codes=pt_failed,
                    elapsed_ms=(_time.monotonic() - _recovery_start) * 1000,
                )

        # ── 3. H0UNCNT0 복원 (핵심 버그 수정) ────────────────────
        if self._price_subscription_service:
            self._price_subscription_service.clear_active_state()
            if self._streaming_stock_repo:
                await self._streaming_stock_repo.clear_active(StreamingType.UNIFIED_PRICE)
            desired_count = len(self._price_subscription_service._refs)
            if desired_count > 0:
                # PT 구독이 없어도 WebSocket 연결 보장 (미연결 시 _rebalance() 실패 방지)
                if self._streaming_service:
                    connected = await self._streaming_service.connect_websocket()
                    if not connected:
                        if self._streaming_logger:
                            self._streaming_logger.log_pt_restore_connect_failed("H0UNCNT0")
                if self._streaming_logger:
                    self._streaming_logger.log_price_restore_start(desired_count)
                await self._price_subscription_service._rebalance()
                if self._streaming_logger:
                    self._streaming_logger.log_price_restore_done(
                        len(self._price_subscription_service._active_codes_price)
                    )

        if self._streaming_logger:
            self._streaming_logger.log_restore(
                codes=pt_codes,
                success=pt_success,
                total=len(pt_codes),
            )

    async def force_reconnect(self, trigger: str = "manual") -> None:
        """WebSocket 연결을 강제로 끊고 모든 구독(PT + H0UNCNT0)을 재연결한다.

        Args:
            trigger: 재연결 원인 ("receive_task_dead" | "data_gap_{N}s" | "market_open" | "manual")
        """
        from repositories.streaming_stock_repo import StreamingType

        pt_codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) if self._streaming_stock_repo else []

        has_price_subs = bool(
            self._price_subscription_service and self._price_subscription_service._refs
        )
        if not pt_codes and not has_price_subs:
            return

        if self._reconnect_lock.locked():
            self._logger.info(f"[WebSocketWatchdog] 재연결 진행 중 — 중복 요청 생략 trigger={trigger}")
            return

        now = time.monotonic()
        if self._last_reconnect_started_ts > 0 and (now - self._last_reconnect_started_ts) < self.RECONNECT_COOLDOWN_SEC:
            self._logger.info(f"[WebSocketWatchdog] 재연결 cooldown 중 — 요청 생략 trigger={trigger}")
            return

        async with self._reconnect_lock:
            now = time.monotonic()
            if self._last_reconnect_started_ts > 0 and (now - self._last_reconnect_started_ts) < self.RECONNECT_COOLDOWN_SEC:
                self._logger.info(f"[WebSocketWatchdog] 재연결 cooldown 중 — 요청 생략 trigger={trigger}")
                return
            self._last_reconnect_started_ts = now

            t_start = self.pm.start_timer()
            if self._streaming_logger:
                self._streaming_logger.log_force_reconnect_start(trigger, pt_codes)

            try:
                await self._streaming_service.disconnect_websocket()
            except Exception as e:
                if self._streaming_logger:
                    self._streaming_logger.log_force_reconnect_disconnect_error(str(e))

            await self._restore_all_subscriptions()

            restored_count, desired_count = self._get_reconnect_restore_counts(pt_codes)
            if self._oas and trigger != "market_open":
                if restored_count >= desired_count:
                    await self._oas.resolve(
                        AlertSource.WEBSOCKET_WATCHDOG, "websocket_watchdog:reconnect",
                        f"재연결 완료 trigger={trigger}",
                    )
                else:
                    await self._oas.report(
                        AlertSource.WEBSOCKET_WATCHDOG, "websocket_watchdog:reconnect",
                        "error", "WebSocket 재연결 미완료",
                        f"trigger={trigger} restored={restored_count}/{desired_count}",
                    )

            if self._streaming_logger:
                from repositories.streaming_stock_repo import StreamingType
                active_pt = len(self._streaming_stock_repo.get_active(StreamingType.PROGRAM_TRADING)) \
                    if self._streaming_stock_repo else 0
                self._streaming_logger.log_reconnect(
                    trigger=trigger,
                    codes=pt_codes,
                    success=active_pt,
                    total=len(pt_codes),
                )
            self.pm.log_timer(f"WebSocketWatchdogTask.force_reconnect({trigger})", t_start)
            if self._streaming_logger:
                self._streaming_logger.log_force_reconnect_done(trigger)

    def _get_reconnect_restore_counts(self, pt_codes: List[str]) -> tuple[int, int]:
        """재연결 후 desired 구독 대비 active 복원 개수를 계산한다."""
        from repositories.streaming_stock_repo import StreamingType

        desired_pt_codes = set(pt_codes)
        active_pt_codes = set()
        if self._streaming_stock_repo:
            active_pt_codes = set(self._streaming_stock_repo.get_active(StreamingType.PROGRAM_TRADING))

        desired_price_codes = set()
        active_price_codes = set()
        if self._price_subscription_service:
            desired_price_codes = set(getattr(self._price_subscription_service, "_refs", {}).keys())
            active_price_codes = set(getattr(self._price_subscription_service, "_active_codes_price", set()))

        desired_count = len(desired_pt_codes) + len(desired_price_codes)
        restored_count = len(desired_pt_codes & active_pt_codes) + len(desired_price_codes & active_price_codes)
        return restored_count, desired_count

    async def force_reconnect_program_trading(self, trigger: str = "manual") -> None:
        """하위호환 alias — force_reconnect()로 위임한다."""
        await self.force_reconnect(trigger=trigger)
