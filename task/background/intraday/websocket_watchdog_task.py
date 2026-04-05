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

# 재구독 시 패킷 간 딜레이 (초) — 증권사 Rate Limit 방지
SUBSCRIBE_DELAY_SEC = 0.2


class WebSocketWatchdogTask(SchedulableTask):
    """프로그램매매 WebSocket 연결을 감시·복원하는 백그라운드 태스크."""

    def __init__(
        self,
        streaming_service: Optional["StreamingService"] = None,
        realtime_data_service: Optional["ProgramTradingStreamService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        notification_service: Optional[NotificationService] = None,
        logger=None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
        streaming_stock_repo: Optional["StreamingStockRepo"] = None,
        price_subscription_service: Optional["PriceSubscriptionService"] = None,
    ):
        self._streaming_service = streaming_service
        self._realtime_data_service = realtime_data_service
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
        if self._realtime_data_service:
            self._realtime_data_service.start_background_tasks()

        # 2. 이전 구독 상태 자동 복원 (PT + H0UNCNT0 통합 복원)
        self._tasks.append(
            asyncio.create_task(self._restore_all_subscriptions())
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

    async def _program_trading_watchdog(self) -> None:
        """프로그램매매 WebSocket 연결 상태를 주기적으로 감시하고, 데이터 수신이 끊기면 재연결."""
        WATCHDOG_INTERVAL = 60   # 감시 주기 (초)
        DATA_GAP_THRESHOLD = 300  # 데이터 미수신 허용 최대 시간 (초) — 소외주 오탐 방지를 위해 120→300

        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                # suspend 상태이면 감시 스킵
                if self._state == TaskState.SUSPENDED:
                    continue

                if not self._realtime_data_service:
                    continue

                # PT 구독 종목 확인 — StreamingStockRepo가 SSOT
                if not self._streaming_stock_repo:
                    continue
                from repositories.streaming_stock_repo import StreamingType
                codes = sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING))
                if not codes:
                    continue  # 구독 중인 종목 없으면 스킵

                market_is_open = bool(self.mcs and await self.mcs.is_market_open_now())
                self._market_open = market_is_open
                if not market_is_open:
                    # 장 마감 시간이면 연결을 명시적으로 종료하여 리소스 정리
                    if self._streaming_service and self._streaming_service.broker.is_websocket_receive_alive():
                        self._logger.info("[워치독] 장 마감 시간이므로 웹소켓 연결을 종료합니다.")
                        await self._streaming_service.disconnect_websocket()
                        self._intentionally_disconnected = True
                    continue

                # 조건 1: 수신 태스크가 죽었는지 확인
                receive_alive = (
                    self._streaming_service is not None
                    and self._streaming_service.broker.is_websocket_receive_alive()
                )

                # 조건 2: 데이터 수신 갭 확인 (한 번이라도 데이터를 받은 적이 있을 때만)
                last_ts = self._realtime_data_service.last_data_ts
                data_gap = (time.time() - last_ts) if last_ts > 0 else 0.0

                reconnect_trigger = None
                if not receive_alive:
                    if self._intentionally_disconnected:
                        self._logger.info("[워치독] 장 시작 — 신규 WebSocket 연결을 수립합니다.")
                        reconnect_trigger = "market_open"
                    else:
                        self._logger.warning("[워치독] WebSocket 수신 태스크가 종료됨. 재연결을 시도합니다.")
                        reconnect_trigger = "receive_task_dead"
                elif last_ts > 0 and data_gap > DATA_GAP_THRESHOLD:
                    self._logger.warning(f"[워치독] {data_gap:.0f}초간 데이터 미수신 (임계값: {DATA_GAP_THRESHOLD}초). 재연결을 시도합니다.")
                    reconnect_trigger = f"data_gap_{data_gap:.0f}s"

                if reconnect_trigger:
                    self._intentionally_disconnected = False
                    await self.force_reconnect(trigger=reconnect_trigger)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[워치독] 오류 발생: {e}")

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

        pt_success = 0
        pt_failed = []
        if pt_codes:
            self._logger.info(f"[워치독] PT 구독 복원 시작: {pt_codes}")
        for code in pt_codes:
            try:
                connected = await self._streaming_service.connect_websocket()
                if not connected:
                    self._logger.warning(f"[워치독] PT 재연결 실패: {code}")
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
                await asyncio.sleep(SUBSCRIBE_DELAY_SEC)
            except Exception as e:
                self._logger.error(f"[워치독] PT 복원 중 오류 ({code}): {e}")
                pt_failed.append(code)

        if pt_failed:
            self._logger.warning(f"[워치독] PT 복원 실패 종목 상태에서 제거: {pt_failed}")
            for code in pt_failed:
                if self._streaming_stock_repo:
                    await self._streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
                if self._streaming_logger:
                    self._streaming_logger.log_pt_unsubscribe(code, reason="restore_failed")
                    self._streaming_logger.log_price_unsubscribe(code, reason="restore_failed")

        if pt_codes:
            self._logger.info(f"[워치독] PT 구독 복원 완료: {pt_success}/{len(pt_codes)}개")

        # ── 3. H0UNCNT0 복원 (핵심 버그 수정) ────────────────────
        if self._price_subscription_service:
            self._price_subscription_service.clear_active_state()
            if self._streaming_stock_repo:
                await self._streaming_stock_repo.clear_active(StreamingType.UNIFIED_PRICE)
            desired_count = len(self._price_subscription_service._refs)
            if desired_count > 0:
                self._logger.info(f"[복원] H0UNCNT0 구독 복원 시작: {desired_count}개 종목")
                await self._price_subscription_service._rebalance()
                self._logger.info(
                    f"[복원] H0UNCNT0 구독 복원 완료: "
                    f"{len(self._price_subscription_service._active_codes)}개 활성"
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
        self._logger.info(f"[워치독] 강제 재연결 시작 (trigger={trigger}, PT 종목: {pt_codes})")

        try:
            await self._streaming_service.disconnect_websocket()
        except Exception as e:
            self._logger.warning(f"[워치독] 기존 연결 종료 중 오류 (무시): {e}")

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
        self._logger.info(f"[워치독] 강제 재연결 완료 (trigger={trigger})")

    async def force_reconnect_program_trading(self, trigger: str = "manual") -> None:
        """하위호환 alias — force_reconnect()로 위임한다."""
        await self.force_reconnect(trigger=trigger)
