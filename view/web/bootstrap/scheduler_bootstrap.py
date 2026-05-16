"""SchedulerBootstrap — `WebAppContext._bootstrap_schedulers()` 본문을 전담한다.

TimeDispatcher 태스크 등록, BackgroundScheduler / ForegroundScheduler 생성과
초기 가격 구독 부트스트랩까지의 범위만 책임진다. StrategyScheduler 생성은
`StrategyFactory` 가 담당한다.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from config.task_config_loader import load_after_market_delays
from interfaces.schedulable_task import TaskPriority
from scheduler.background_scheduler import BackgroundScheduler
from scheduler.foreground_scheduler import ForegroundScheduler

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class SchedulerBootstrap:
    """`WebAppContext` 의 스케줄러 인프라 초기화 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx
        try:
            _delays = load_after_market_delays()
            for task, priority in [
                (ctx.ranking_task,                     TaskPriority.LOW),
                (ctx.minervini_update_task,             TaskPriority.LOW),
                (ctx.daily_price_collector_task,        TaskPriority.LOW),
                (ctx.ohlcv_update_task,                 TaskPriority.LOW),
                (ctx.premium_watchlist_generator_task,  TaskPriority.LOW),
                (ctx.newhigh_task,                      TaskPriority.LOW),
                (ctx.log_cleanup_task,                  TaskPriority.MAINTENANCE),
                (ctx.strategy_log_report_task,          TaskPriority.LOW),
                (ctx.opening_position_reconcile_task,   TaskPriority.HIGH),
                (ctx.after_market_reconcile_task,       TaskPriority.LOW),
            ]:
                if task:
                    ctx.time_dispatcher.register_task(
                        task.task_name, priority, delay_sec=_delays.get(task.task_name, 0)
                    )

            ctx.background_scheduler = BackgroundScheduler(
                logger=ctx.logger,
                performance_profiler=ctx.pm,
                worker_pool=ctx.worker_pool,
                time_dispatcher=ctx.time_dispatcher,
            )
            for task in [
                ctx.ranking_task,
                ctx.minervini_update_task,
                ctx.websocket_watchdog_task,
                ctx.pre_market_health_check_task,
                ctx.daily_price_collector_task,
                ctx.ohlcv_update_task,
                ctx.premium_watchlist_generator_task,
                ctx.cache_warmup_task,
                ctx.log_cleanup_task,
                ctx.newhigh_task,
                ctx.notification_queue_task,
                ctx.strategy_log_report_task,
                ctx.opening_position_reconcile_task,
                ctx.after_market_reconcile_task,
            ]:
                if task:
                    ctx.background_scheduler.register(task)

            ctx.foreground_scheduler = ForegroundScheduler(
                background_scheduler=ctx.background_scheduler,
                logger=ctx.logger,
                performance_profiler=ctx.pm,
            )
            asyncio.create_task(ctx._initialize_price_subscriptions())
        except Exception as e:
            ctx.logger.critical(f"[SchedulerBootstrap] 초기화 실패: {e}", exc_info=True)
            raise
