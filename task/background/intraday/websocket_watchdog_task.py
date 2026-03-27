# task/background/intraday/websocket_watchdog_task.py
"""
WebSocket 연결 감시 및 자동 복원 태스크.
WebSocket 수신 태스크 상태를 주기적으로 감시하고,
데이터 수신이 끊기면 재연결한다.

감시 대상:
  - 프로그램매매(H0STPGM0) 구독
  - 체결가(H0UNCNT0) 구독
  → 재연결 시 RealtimeSubscriptionService.restore_all_subscriptions() 로 모두 복원
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Callable, TYPE_CHECKING

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from core.performance_profiler import PerformanceProfiler
from services.notification_service import NotificationService

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from services.program_trading_stream_service import ProgramTradingStreamService
    from services.realtime_subscription_service import RealtimeSubscriptionService
    from services.market_calendar_service import MarketCalendarService


class WebSocketWatchdogTask(SchedulableTask):
    """WebSocket 연결(프로그램매매 + 체결가)을 감시·복원하는 백그라운드 태스크."""

    def __init__(
        self,
        streaming_service: Optional["StreamingService"] = None,
        realtime_data_service: Optional["ProgramTradingStreamService"] = None,
        subscription_service: Optional["RealtimeSubscriptionService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        logger=None,
    ):
        self._streaming_service = streaming_service
        self._realtime_data_service = realtime_data_service   # last_data_ts 헬스체크용
        self._subscription_service = subscription_service     # 구독 상태 관리 + 복원
        self.mcs = market_calendar_service
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._ns = notification_service
        self._logger = logger or logging.getLogger(__name__)

        # SchedulableTask 상태
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._realtime_callback: Optional[Callable] = None
        self._market_open: Optional[bool] = None  # 가장 최근 시장 개장 여부 (워치독 루프에서 갱신)

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
        """WebSocket 워치독 + 구독 복원 태스크를 시작한다.

        Note: realtime_callback은 start() 호출 전에 _realtime_callback 속성으로 설정해야 한다.
        """
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING

        # 1. 실시간 데이터 매니저 백그라운드 태스크 (데이터 정리 등)
        if self._realtime_data_service:
            self._realtime_data_service.start_background_tasks()

        # 2. 이전 구독 상태 자동 복원 (PT + 체결가 모두)
        if self._subscription_service and (
            self._subscription_service.has_program_trading_subscriptions()
            or self._subscription_service._active_codes
        ):
            self._tasks.append(
                asyncio.create_task(self._restore_subscriptions())
            )

        # 3. WebSocket 연결 상태 워치독
        self._tasks.append(
            asyncio.create_task(self._websocket_watchdog())
        )

        self._logger.info(f"WebSocketWatchdogTask 시작: {len(self._tasks)}개 태스크")

    async def stop(self) -> None:
        """모든 워치독 태스크를 취소하고 정리한다."""
        self._logger.info(f"WebSocketWatchdogTask 종료 시작: {len(self._tasks)}개 태스크")

        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()

        # 실시간 데이터 매니저 종료
        if self._realtime_data_service:
            await self._realtime_data_service.shutdown()

        self._state = TaskState.STOPPED
        self._logger.info("WebSocketWatchdogTask 종료 완료")

    async def suspend(self) -> None:
        """워치독을 일시 중지한다."""
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED
            self._logger.info("WebSocketWatchdogTask 일시 중지")

    async def resume(self) -> None:
        """워치독을 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING
            self._logger.info("WebSocketWatchdogTask 재개")

    # ── 구독 복원 ──────────────────────────────────────────────

    async def _restore_subscriptions(self) -> None:
        """앱 시작 시 이전 구독 상태를 자동 복원 (백그라운드)."""
        if not self._subscription_service:
            return

        pt_codes = self._subscription_service.get_program_trading_codes()
        price_codes = list(self._subscription_service._active_codes)
        self._logger.info(f"구독 복원 시작 — PT: {pt_codes}, 체결가: {len(price_codes)}개")

        try:
            connected = await self._streaming_service.connect_websocket(self._realtime_callback)
            if not connected:
                self._logger.warning("구독 복원 실패 (WebSocket 연결 불가)")
                return
            await self._subscription_service.restore_all_subscriptions()
        except Exception as e:
            self._logger.error(f"구독 복원 중 오류: {e}")

    # ── WebSocket 워치독 ────────────────────────────────────────

    async def _websocket_watchdog(self) -> None:
        """WebSocket 연결 상태를 주기적으로 감시하고, 이상이 감지되면 재연결."""
        WATCHDOG_INTERVAL = 60   # 감시 주기 (초)
        DATA_GAP_THRESHOLD = 120  # 데이터 미수신 허용 최대 시간 (초) — PT 구독 시에만 적용

        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                # suspend 상태이면 감시 스킵
                if self._state == TaskState.SUSPENDED:
                    continue

                if not self._subscription_service:
                    continue

                # 구독 중인 종목이 없으면 스킵
                has_pt = self._subscription_service.has_program_trading_subscriptions()
                has_price = bool(self._subscription_service._active_codes)
                if not has_pt and not has_price:
                    continue

                market_is_open = bool(self.mcs and await self.mcs.is_market_open_now())
                self._market_open = market_is_open
                if not market_is_open:
                    # 장 마감 시간이면 연결을 명시적으로 종료하여 리소스 정리
                    if self._streaming_service and self._streaming_service.broker.is_websocket_receive_alive():
                        self._logger.info("[워치독] 장 마감 시간이므로 웹소켓 연결을 종료합니다.")
                        await self._streaming_service.disconnect_websocket()
                    continue

                # 조건 1: 수신 태스크가 죽었는지 확인
                receive_alive = (
                    self._streaming_service is not None
                    and self._streaming_service.broker.is_websocket_receive_alive()
                )

                # 조건 1b: 루프는 살아있지만 현재 재연결 시도 중인지 확인 (간섭 방지)
                is_connected = (
                    self._streaming_service is not None
                    and self._streaming_service.broker.is_websocket_connected()
                )
                if receive_alive and not is_connected:
                    self._logger.debug("[워치독] 수신 루프가 재연결 중입니다. 워치독 개입 생략.")
                    continue

                # 조건 2: PT 구독 중일 때만 데이터 수신 갭 확인
                needs_reconnect = False
                if not receive_alive:
                    self._logger.warning("[워치독] WebSocket 수신 태스크가 종료됨. 재연결을 시도합니다.")
                    needs_reconnect = True
                elif has_pt and self._realtime_data_service:
                    last_ts = self._realtime_data_service.last_data_ts
                    data_gap = (time.time() - last_ts) if last_ts > 0 else float('inf')
                    if data_gap > DATA_GAP_THRESHOLD:
                        self._logger.warning(
                            f"[워치독] {data_gap:.0f}초간 PT 데이터 미수신 "
                            f"(임계값: {DATA_GAP_THRESHOLD}초). 재연결을 시도합니다."
                        )
                        needs_reconnect = True

                if needs_reconnect:
                    await self.force_reconnect_all()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[워치독] 오류 발생: {e}")

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현).

        Watchdog 태스크는 배치 진행률이 없으므로 연결 상태 정보를 반환한다.
        """
        pt_count = 0
        price_count = 0
        if self._subscription_service:
            pt_count = len(self._subscription_service.get_program_trading_codes())
            price_count = len(self._subscription_service._active_codes)

        last_ts = 0.0
        data_gap = None
        if self._realtime_data_service:
            last_ts = getattr(self._realtime_data_service, "last_data_ts", 0.0)
            if last_ts > 0:
                data_gap = round(time.time() - last_ts, 1)

        return {
            "running": self._state == TaskState.RUNNING,
            "subscribed_pt_codes": pt_count,
            "subscribed_price_codes": price_count,
            "data_gap_sec": data_gap,
            "market_open": self._market_open,
        }

    async def force_reconnect_all(self) -> None:
        """WebSocket 연결을 강제로 끊고 재연결 + PT/체결가 모든 구독 복원."""
        if not self._subscription_service:
            return

        has_pt = self._subscription_service.has_program_trading_subscriptions()
        has_price = bool(self._subscription_service._active_codes)
        if not has_pt and not has_price:
            return

        t_start = self.pm.start_timer()
        pt_codes = self._subscription_service.get_program_trading_codes()
        price_count = len(self._subscription_service._active_codes)
        self._logger.info(
            f"[워치독] 강제 재연결 시작 (PT: {pt_codes}, 체결가: {price_count}개)"
        )

        try:
            # 1. 기존 WebSocket 연결 강제 종료
            await self._streaming_service.disconnect_websocket()
        except Exception as e:
            self._logger.warning(f"[워치독] 기존 연결 종료 중 오류 (무시): {e}")

        # 2. 재연결
        try:
            connected = await self._streaming_service.connect_websocket(self._realtime_callback)
            if not connected:
                self._logger.warning("[워치독] 재연결 실패")
                self.pm.log_timer("WebSocketWatchdogTask.force_reconnect_all(FAILED)", t_start)
                return
        except Exception as e:
            self._logger.error(f"[워치독] 재연결 중 오류: {e}")
            return

        # 3. 모든 구독 복원 (PT + 체결가)
        await self._subscription_service.restore_all_subscriptions()

        self.pm.log_timer(
            f"WebSocketWatchdogTask.force_reconnect_all(PT:{len(pt_codes)}, price:{price_count})",
            t_start
        )
        self._logger.info("[워치독] 강제 재연결 완료")
