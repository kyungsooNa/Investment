"""장마감 replay/coverage 백테스트 태스크 조립."""

import os
import sys
from typing import TYPE_CHECKING

from scheduler.strategy_scheduler_store import StrategySchedulerStore
from services.newhigh_strategy_coverage_backtest_service import (
    NewHighStrategyCoverageBacktestService,
)
from services.post_market_replay_audit_service import PostMarketReplayAuditService
from services.validation_campaign_service import (
    SubprocessStrategyRunner,
    ValidationCampaignService,
)
from scripts.run_backtest import ACTIVE_BACKTEST_STRATEGIES
from strategies.debug.strategy_debug_runner import StrategyDebugRunner
from task.background.after_market.newhigh_strategy_coverage_backtest_task import (
    NewHighStrategyCoverageBacktestTask,
)
from task.background.after_market.post_market_replay_audit_task import (
    PostMarketReplayAuditTask,
)
from task.background.after_market.weekly_backtest_campaign_task import (
    WeeklyBacktestCampaignTask,
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

        raw_config = ctx.full_config
        if hasattr(raw_config, "model_dump"):
            raw_config = raw_config.model_dump()
        weekly_config = (raw_config or {}).get("weekly_backtest_campaign") or {}
        if not weekly_config.get("enabled", True):
            ctx.weekly_backtest_campaign_task = None
            return

        replay_dir = weekly_config.get("replay_dir", "data/backtest_microstructure")
        strategies = weekly_config.get("strategies") or ACTIVE_BACKTEST_STRATEGIES
        scheduler_store = StrategySchedulerStore(logger=ctx.logger)
        ctx.weekly_backtest_campaign_task = WeeklyBacktestCampaignTask(
            campaign_service=ValidationCampaignService(
                replay_dir=replay_dir,
                strategies=strategies,
                strategy_runner=SubprocessStrategyRunner(
                    python_executable=sys.executable,
                    paper=bool(weekly_config.get("use_paper_data", False)),
                ),
            ),
            scheduler_store=scheduler_store,
            min_valid_dates=int(weekly_config.get("min_valid_dates", 30)),
            mcs=ctx._mcs,
            market_clock=ctx.market_clock,
            logger=ctx.logger,
            worker_pool=ctx.worker_pool,
        )
