"""SchedulerBootstrap — `WebAppContext._bootstrap_schedulers()` 본문을 전담한다.

TimeDispatcher 태스크 등록, BackgroundScheduler / ForegroundScheduler 생성과
초기 가격 구독 부트스트랩까지의 범위만 책임진다. StrategyScheduler 생성은
`StrategyFactory` 가 담당한다.

`WebAppContext.runtime_mode` 에 따라 task 등록을 그룹별로 분기한다.
BackgroundScheduler / ForegroundScheduler 생성 자체는 mode 와 무관하게 항상 수행한다
(foreground middleware 가 WEB API rate-limit 경합 제어에 의존).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config.task_config_loader import load_after_market_delays
from interfaces.schedulable_task import TaskPriority
from scheduler.background_scheduler import BackgroundScheduler
from scheduler.foreground_scheduler import ForegroundScheduler
from view.web.bootstrap.runtime_mode import RuntimeMode
from view.web.market_mode_utils import is_market_enabled

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class SchedulerBootstrap:
    """`WebAppContext` 의 스케줄러 인프라 초기화 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context
        self._delays: dict[str, int] = {}

    def run(self) -> None:
        ctx = self._ctx
        mode = ctx.runtime_mode
        try:
            self._delays = load_after_market_delays()

            self._create_background_scheduler()

            if mode & RuntimeMode.WEB:
                self._register_web_tasks()
            if mode & RuntimeMode.TRADING:
                self._register_trading_tasks()
            if mode & RuntimeMode.BATCH:
                self._register_batch_tasks()
            # Phase 3c: overseas_us 가 enabled_market_modes 에 포함되면 dry-run 태스크를
            # 별도 등록한다. active=overseas_us(batch off) 와 active=domestic 공존 양쪽을
            # 모두 포괄한다. 미국장 마감 cron 으로 자체 트리거되므로 KST 배치와 무관.
            if is_market_enabled(ctx, "overseas_us"):
                self._register_overseas_tasks()
            # websocket_watchdog: WEB | TRADING 어느 한쪽이라도 켜져 있으면 한 번만 등록.
            if mode & (RuntimeMode.WEB | RuntimeMode.TRADING):
                self._register_websocket_watchdog()

            self._create_foreground_scheduler()

        except Exception as e:
            ctx.logger.critical(f"[SchedulerBootstrap] 초기화 실패: {e}", exc_info=True)
            raise

    def _create_background_scheduler(self) -> None:
        ctx = self._ctx
        # 시장별 TimeDispatcher(KST + US)를 모두 BackgroundScheduler에 넘겨 동시 구동한다.
        dispatchers = [
            d for d in (ctx.time_dispatcher, getattr(ctx, "time_dispatcher_us", None))
            if d is not None
        ]
        ctx.background_scheduler = BackgroundScheduler(
            logger=ctx.logger,
            performance_profiler=ctx.pm,
            worker_pool=ctx.worker_pool,
            time_dispatchers=dispatchers,
        )

    def _create_foreground_scheduler(self) -> None:
        ctx = self._ctx
        ctx.foreground_scheduler = ForegroundScheduler(
            background_scheduler=ctx.background_scheduler,
            logger=ctx.logger,
            performance_profiler=ctx.pm,
        )

    def _register(
        self, task, priority: TaskPriority | None = None, market: str = "domestic"
    ) -> None:
        if not task:
            return
        ctx = self._ctx
        if priority is not None:
            dispatcher = ctx.time_dispatcher
            if market == "overseas_us":
                dispatcher = getattr(ctx, "time_dispatcher_us", None)
            if dispatcher is not None:
                dispatcher.register_task(
                    task.task_name, priority, delay_sec=self._delays.get(task.task_name, 0)
                )
        ctx.background_scheduler.register(task)

    def _optional_task(self, attr_name: str):
        return getattr(self._ctx, "__dict__", {}).get(attr_name)

    def _register_web_tasks(self) -> None:
        ctx = self._ctx
        # NotificationQueueTask 는 TimeDispatcher 등록 대상이 아님 (always-on)
        self._register(ctx.notification_queue_task)

    def _register_trading_tasks(self) -> None:
        ctx = self._ctx
        self._register(ctx.pre_market_health_check_task)
        self._register(ctx.opening_position_reconcile_task, TaskPriority.HIGH)
        self._register(ctx.cache_warmup_task)

    def _register_batch_tasks(self) -> None:
        ctx = self._ctx
        self._register(ctx.ranking_task, TaskPriority.LOW)
        # 자체 AfterMarketLoop 사용: 한국장/미국장 각각의 cron timezone이 필요해
        # KST TimeDispatcher에는 등록하지 않는다.
        self._register(self._optional_task("market_cap_gap_report_kr_task"))
        self._register(self._optional_task("market_cap_gap_report_us_task"))
        self._register(ctx.minervini_update_task, TaskPriority.LOW)
        self._register(ctx.daily_price_collector_task, TaskPriority.LOW)
        self._register(ctx.ohlcv_update_task, TaskPriority.LOW)
        self._register(ctx.premium_watchlist_generator_task, TaskPriority.LOW)
        self._register(ctx.newhigh_task, TaskPriority.LOW)
        self._register(ctx.theme_classification_task, TaskPriority.LOW)
        self._register(self._optional_task("theme_daily_leader_report_task"), TaskPriority.LOW)
        self._register(ctx.log_cleanup_task, TaskPriority.MAINTENANCE)
        self._register(ctx.post_market_replay_audit_task, TaskPriority.LOW)
        self._register(ctx.strategy_log_report_task, TaskPriority.LOW)
        self._register(ctx.after_market_reconcile_task, TaskPriority.LOW)
        # 자체 AfterMarketLoop 사용 (KST 16:05): market_cap_gap 과 동일 패턴.
        self._register(self._optional_task("microstructure_capture_task"))

    def _register_overseas_tasks(self) -> None:
        # 해외 VBO dry-run (주문 경로 없음). 미구성 시 _register 가 no-op.
        # 미국장 TimeDispatcher(time_dispatcher_us)에 등록 → NY 마감 후 delay 만큼 대기 뒤
        # 티켓 발행(task_config 의 overseas_vbo_dryrun delay 로 16:30 ET 효과 트리거).
        self._register(
            self._optional_task("overseas_dryrun_task"),
            TaskPriority.LOW,
            market="overseas_us",
        )

    def _register_websocket_watchdog(self) -> None:
        # WebSocket watchdog 은 TimeDispatcher 등록 대상이 아님 (continuous monitor).
        self._register(self._ctx.websocket_watchdog_task)
