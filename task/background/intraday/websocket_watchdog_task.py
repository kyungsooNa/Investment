# task/background/websocket_watchdog_task.py
"""
프로그램매매 WebSocket 연결 감시 및 자동 복원 태스크.
WebSocket 수신 태스크 상태를 주기적으로 감시하고,
데이터 수신이 끊기면 재연결한다.
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
    from services.realtime_data_service import RealtimeDataService
    from services.market_calendar_service import MarketCalendarService


class WebSocketWatchdogTask(SchedulableTask):
    """프로그램매매 WebSocket 연결을 감시·복원하는 백그라운드 태스크."""

    def __init__(
        self,
        streaming_service: Optional["StreamingService"] = None,
        realtime_data_service: Optional["RealtimeDataService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        logger=None,
    ):
        self._streaming_service = streaming_service
        self._realtime_data_service = realtime_data_service
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

        # 2. 이전 구독 상태 자동 복원
        if self._realtime_data_service:
            saved_codes = self._realtime_data_service.get_subscribed_codes()
            if saved_codes:
                self._tasks.append(
                    asyncio.create_task(self._restore_program_trading(saved_codes))
                )

        # 3. 프로그램매매 연결 상태 워치독
        self._tasks.append(
            asyncio.create_task(self._program_trading_watchdog())
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

    # ── 프로그램매매 워치독 / 복원 / 재연결 ──────────────────────

    async def _restore_program_trading(self, codes: list) -> None:
        """앱 시작 시 이전 구독 상태를 자동 복원 (백그라운드)."""
        self._logger.info(f"프로그램매매 구독 복원 시작: {codes}")
        success_count = 0
        failed_codes = []
        for code in codes:
            try:
                connected = await self._streaming_service.connect_websocket(self._realtime_callback)
                if not connected:
                    self._logger.warning(f"프로그램매매 복원 실패 (WebSocket 연결 불가): {code}")
                    failed_codes.append(code)
                    continue
                await self._streaming_service.subscribe_program_trading(code)
                await self._streaming_service.subscribe_realtime_price(code)
                success_count += 1
            except Exception as e:
                self._logger.error(f"프로그램매매 복원 중 오류 ({code}): {e}")
                failed_codes.append(code)

        if failed_codes:
            self._logger.warning(f"복원에 실패한 구독 종목을 상태에서 제거합니다: {failed_codes}")
            for code in failed_codes:
                self._realtime_data_service.remove_subscribed_code(code)

        self._logger.info(f"프로그램매매 구독 복원 완료: {success_count}/{len(codes)}개 종목")

    async def _program_trading_watchdog(self) -> None:
        """프로그램매매 WebSocket 연결 상태를 주기적으로 감시하고, 데이터 수신이 끊기면 재연결."""
        WATCHDOG_INTERVAL = 60   # 감시 주기 (초)
        DATA_GAP_THRESHOLD = 120  # 데이터 미수신 허용 최대 시간 (초)

        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                # suspend 상태이면 감시 스킵
                if self._state == TaskState.SUSPENDED:
                    continue

                if not self._realtime_data_service:
                    continue

                codes = self._realtime_data_service.get_subscribed_codes()
                if not codes:
                    continue  # 구독 중인 종목 없으면 스킵

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

                # 조건 2: 데이터 수신 갭 확인
                last_ts = self._realtime_data_service.last_data_ts
                data_gap = (time.time() - last_ts) if last_ts > 0 else float('inf')

                needs_reconnect = False
                if not receive_alive:
                    self._logger.warning(f"[워치독] WebSocket 수신 태스크가 종료됨. 재연결을 시도합니다.")
                    needs_reconnect = True
                elif data_gap > DATA_GAP_THRESHOLD:
                    self._logger.warning(f"[워치독] {data_gap:.0f}초간 데이터 미수신 (임계값: {DATA_GAP_THRESHOLD}초). 재연결을 시도합니다.")
                    needs_reconnect = True

                if needs_reconnect:
                    await self.force_reconnect_program_trading()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[워치독] 오류 발생: {e}")

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현).

        Watchdog 태스크는 배치 진행률이 없으므로 연결 상태 정보를 반환한다.
        """
        subscribed = 0
        if self._realtime_data_service:
            codes = self._realtime_data_service.get_subscribed_codes()
            subscribed = len(codes) if codes else 0

        last_ts = 0.0
        data_gap = None
        if self._realtime_data_service:
            last_ts = getattr(self._realtime_data_service, "last_data_ts", 0.0)
            if last_ts > 0:
                data_gap = round(time.time() - last_ts, 1)

        return {
            "running": self._state == TaskState.RUNNING,
            "subscribed_codes": subscribed,
            "data_gap_sec": data_gap,
            "market_open": self._market_open,
        }

    async def force_reconnect_program_trading(self) -> None:
        """WebSocket 연결을 강제로 끊고 재연결 + 재구독."""
        if not self._realtime_data_service:
            return

        codes = self._realtime_data_service.get_subscribed_codes()
        if not codes:
            return

        t_start = self.pm.start_timer()
        self._logger.info(f"[워치독] 강제 재연결 시작 (구독 종목: {codes})")
        try:
            # 1. 기존 WebSocket 연결 강제 종료
            await self._streaming_service.disconnect_websocket()
        except Exception as e:
            self._logger.warning(f"[워치독] 기존 연결 종료 중 오류 (무시): {e}")

        # 2. 새 연결 + 재구독
        success_count = 0
        failed_codes = []
        for code in codes:
            try:
                connected = await self._streaming_service.connect_websocket(self._realtime_callback)
                if not connected:
                    self._logger.warning(f"[워치독] 재연결 실패: {code}")
                    failed_codes.append(code)
                    continue
                await self._streaming_service.subscribe_program_trading(code)
                await self._streaming_service.subscribe_realtime_price(code)
                success_count += 1
            except Exception as e:
                self._logger.error(f"[워치독] 재구독 중 오류 ({code}): {e}")
                failed_codes.append(code)

        if failed_codes:
            self._logger.warning(f"[워치독] 재구독에 실패한 종목을 상태에서 제거합니다: {failed_codes}")
            for code in failed_codes:
                self._realtime_data_service.remove_subscribed_code(code)

        self.pm.log_timer(f"WebSocketWatchdogTask.force_reconnect_program_trading({success_count}/{len(codes)})", t_start)
        self._logger.info(f"[워치독] 강제 재연결 완료: {success_count}/{len(codes)}개 종목")
