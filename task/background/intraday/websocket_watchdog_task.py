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

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from services.program_trading_stream_service import ProgramTradingStreamService
    from services.market_calendar_service import MarketCalendarService
    from services.price_subscription_service import PriceSubscriptionService
    from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType
    from core.logger import StreamingEventLogger

class WebSocketWatchdogTask(SchedulableTask):
    """프로그램매매 WebSocket 연결을 감시·복원하는 백그라운드 태스크."""

    # 재구독 시 패킷 간 딜레이 (초) — 증권사 Rate Limit 방지
    SUBSCRIBE_DELAY_SEC = 0.2

    def __init__(
        self,
        streaming_service: Optional["StreamingService"] = None,
        program_trading_stream_service: Optional["ProgramTradingStreamService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        logger=None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
        streaming_stock_repo: Optional["StreamingStockRepo"] = None,
        price_subscription_service: Optional["PriceSubscriptionService"] = None,
    ):
        self._streaming_service = streaming_service
        self._program_trading_stream_service = program_trading_stream_service
        self.mcs = market_calendar_service
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._ns = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._streaming_logger = streaming_logger
        self._streaming_stock_repo = streaming_stock_repo
        self._price_subscription_service = price_subscription_service

        # SchedulableTask 상태
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._market_open: Optional[bool] = None  # 가장 최근 시장 개장 여부 (워치독 루프에서 갱신)
        self._intentionally_disconnected: bool = False  # 장 마감으로 인한 의도적 연결 종료 여부

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
        self._state = TaskState.RUNNING

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
        WATCHDOG_INTERVAL = 60   # 감시 주기 (초)
        DATA_GAP_THRESHOLD = 300  # PT 데이터 미수신 허용 최대 시간 (초)

        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                # suspend 상태이면 감시 스킵
                if self._state == TaskState.SUSPENDED:
                    continue

                # PT 구독 종목 확인 — StreamingStockRepo가 SSOT
                from repositories.streaming_stock_repo import StreamingType
                pt_codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) \
                    if self._streaming_stock_repo else []

                # 실시간 체결가(H0UNCNT0) 구독 여부 확인
                has_price_subs = bool(
                    self._price_subscription_service and self._price_subscription_service._refs
                )

                # PT 구독도 없고 실시간 체결가 구독도 없으면 감시 불필요
                if not pt_codes and not has_price_subs:
                    continue

                market_is_open = bool(self.mcs and await self.mcs.is_market_open_now())
                self._market_open = market_is_open
                if not market_is_open:
                    # 장 마감 시간이면 연결을 명시적으로 종료하여 리소스 정리
                    if self._streaming_service and self._streaming_service.broker.is_websocket_receive_alive():
                        if self._streaming_logger:
                            self._streaming_logger.log_market_closed_disconnect()
                        await self._streaming_service.disconnect_websocket()
                        self._intentionally_disconnected = True
                    continue

                # 조건 1: 수신 태스크가 죽었는지 확인 (PT/체결가 공통)
                receive_alive = (
                    self._streaming_service is not None
                    and self._streaming_service.broker.is_websocket_receive_alive()
                )

                # 조건 2: PT 데이터 수신 갭 확인 (PT 종목이 있을 때만 — last_data_ts가 PT 기준)
                data_gap = 0.0
                if pt_codes and self._program_trading_stream_service:
                    last_ts = self._program_trading_stream_service.last_data_ts
                    data_gap = (time.time() - last_ts) if last_ts > 0 else 0.0

                if self._streaming_logger:
                    self._streaming_logger.log_watchdog_check(
                        receive_alive=receive_alive,
                        data_gap_sec=data_gap,
                        market_open=market_is_open,
                        subscribed_count=len(pt_codes),
                    )

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
                elif pt_codes and data_gap > DATA_GAP_THRESHOLD:
                    # 수신 태스크는 살아있지만 PT 데이터가 임계값 이상 안 오는 경우
                    if self._streaming_logger:
                        self._streaming_logger.log_pt_data_gap(data_gap, DATA_GAP_THRESHOLD)
                    reconnect_trigger = f"data_gap_{data_gap:.0f}s"

                if reconnect_trigger:
                    self._intentionally_disconnected = False
                    await self.force_reconnect(trigger=reconnect_trigger)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._streaming_logger:
                    self._streaming_logger.log_watchdog_error(str(e))

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현).

        Watchdog 태스크는 배치 진행률이 없으므로 연결 상태 정보를 반환한다.
        """
        subscribed = 0
        if self._streaming_stock_repo:
            from repositories.streaming_stock_repo import StreamingType
            subscribed = len(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING))

        last_ts = 0.0
        data_gap = None
        if self._program_trading_stream_service:
            last_ts = getattr(self._program_trading_stream_service, "last_data_ts", 0.0)
            if last_ts > 0:
                data_gap = round(time.time() - last_ts, 1)

        return {
            "running": self._state == TaskState.RUNNING,
            "subscribed_codes": subscribed,
            "data_gap_sec": data_gap,
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
        for code in pt_codes:
            try:
                connected = await self._streaming_service.connect_websocket()
                if not connected:
                    if self._streaming_logger:
                        self._streaming_logger.log_pt_restore_connect_failed(code)
                    pt_failed.append(code)
                    continue
                await self._streaming_service.subscribe_program_trading(code)
                if self._streaming_logger:
                    self._streaming_logger.log_pt_subscribe(code, reason="restore")
                await self._streaming_service.subscribe_realtime_price(code)
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
            if self._streaming_logger:
                self._streaming_logger.log_pt_restore_failed_removed(pt_failed)
            for code in pt_failed:
                if self._streaming_stock_repo:
                    await self._streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
                if self._streaming_logger:
                    self._streaming_logger.log_pt_unsubscribe(code, reason="restore_failed")
                    self._streaming_logger.log_price_unsubscribe(code, reason="restore_failed")

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

        t_start = self.pm.start_timer()
        if self._streaming_logger:
            self._streaming_logger.log_force_reconnect_start(trigger, pt_codes)

        try:
            await self._streaming_service.disconnect_websocket()
        except Exception as e:
            if self._streaming_logger:
                self._streaming_logger.log_force_reconnect_disconnect_error(str(e))

        await self._restore_all_subscriptions()

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

    async def force_reconnect_program_trading(self, trigger: str = "manual") -> None:
        """하위호환 alias — force_reconnect()로 위임한다."""
        await self.force_reconnect(trigger=trigger)
