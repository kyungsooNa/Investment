"""장마감 replay/coverage 백테스트 태스크 조립."""

import os
from typing import TYPE_CHECKING

from scheduler.strategy_scheduler_store import StrategySchedulerStore
from services.newhigh_strategy_coverage_backtest_service import (
    NewHighStrategyCoverageBacktestService,
)
from services.post_market_replay_audit_service import PostMarketReplayAuditService
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from task.background.after_market.newhigh_strategy_coverage_backtest_task import (
    NewHighStrategyCoverageBacktestTask,
)
from task.background.after_market.post_market_replay_audit_task import (
    PostMarketReplayAuditTask,
)

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class BacktestTaskBootstrap:
    """백테스트 감사 및 신고가 커버리지 태스크를 컨텍스트에 조립한다."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx
        program_provider = getattr(ctx.broker, "_client", ctx.broker)

        ctx.post_market_replay_audit_task = PostMarketReplayAuditTask(
            audit_service=PostMarketReplayAuditService(
                stock_query_service=ctx.stock_query_service,
                universe_service=ctx.oneil_universe_service,
                indicator_service=ctx.indicator_service,
                market_clock=ctx.market_clock,
                backtest_journal_repository=ctx.backtest_journal_repository,
                scheduler_store=StrategySchedulerStore(logger=ctx.logger),
                debug_runner_factory=StrategyDebugRunner,
                virtual_trade_service=ctx.virtual_trade_service,
                log_dir=os.path.join(ctx.logger.log_dir, "strategies"),
                program_provider=program_provider,
                env=ctx.env,
                logger=ctx.logger,
            ),
            mcs=ctx._mcs,
            market_clock=ctx.market_clock,
            logger=ctx.logger,
            worker_pool=ctx.worker_pool,
        )
        ctx.newhigh_strategy_coverage_backtest_task = NewHighStrategyCoverageBacktestTask(
            coverage_service=NewHighStrategyCoverageBacktestService(
                stock_repository=ctx.stock_repository,
                stock_query_service=ctx.stock_query_service,
                universe_service=ctx.oneil_universe_service,
                indicator_service=ctx.indicator_service,
                market_clock=ctx.market_clock,
                backtest_journal_repository=ctx.backtest_journal_repository,
                debug_runner_factory=StrategyDebugRunner,
                program_provider=program_provider,
                env=ctx.env,
                logger=ctx.logger,
            ),
            mcs=ctx._mcs,
            market_clock=ctx.market_clock,
            logger=ctx.logger,
            worker_pool=ctx.worker_pool,
        )
