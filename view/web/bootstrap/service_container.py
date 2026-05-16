"""ServiceContainer — `WebAppContext._bootstrap_services()` 본문을 전담한다.

저장소, 도메인 서비스, 백그라운드 태스크, 주문 파이프라인, 유니버스
서비스까지 8개의 try-except 블록을 그대로 옮긴다. 후주입 패턴
(`_minervini_update_task`, `data_quality_service.set_price_stream_service`,
`streaming_service.set_streaming_stock_repo` 등)도 그대로 유지한다.
세부 분해와 후주입 정리는 후속 PR 에서 진행한다.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config.config_loader import (
    DataQualityConfig,
    OrderPolicyConfig,
    PositionSizingConfig,
    RiskGateConfig,
)
from core.account_snapshot import AccountSnapshotCache
from core.cache.cache_store import CacheStore
from core.performance_profiler import PerformanceProfiler
from repositories.rs_rating_repository import RSRatingRepository
from repositories.stock_repository import StockRepository
from repositories.streaming_stock_repo import StreamingStockRepo
from scheduler.dispatcher.time_dispatcher import TimeDispatcher
from scheduler.ticket_queue.dlq_manager import DlqManager
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.worker.worker_pool import WorkerPool
from services.data_quality_service import DataQualityService
from services.execution_flow_service import ExecutionFlowService
from services.indicator_service import IndicatorService
from services.deferred_order_queue import DeferredOrderQueue
from services.market_data_service import MarketDataService
from services.minervini_stage_service import MinerviniStageService
from services.naver_finance_scraper_service import NaverFinanceScraperService
from services.newhigh_service import NewHighService
from services.oneil_universe_service import OneilUniverseService
from services.opening_position_reconcile_service import OpeningPositionReconcileService
from services.order_execution_service import OrderExecutionService
from services.order_policy_service import OrderPolicyService
from services.position_sizing_service import PositionSizingService
from services.price_stream_service import PriceStreamService
from services.price_subscription_service import PriceSubscriptionService
from services.risk_gate_service import RiskGateService
from services.rs_rating_service import RSRatingService
from services.stock_query_service import StockQueryService
from services.streaming_service import StreamingService
from services.strategy_log_report_service import StrategyLogReportService
from task.background.after_market.after_market_reconcile_task import AfterMarketReconcileTask
from task.background.after_market.cache_warmup_task import CacheWarmupTask
from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
from task.background.after_market.log_cleanup_task import LogCleanupTask
from task.background.after_market.minervini_update_task import MinerviniUpdateTask
from task.background.after_market.newhigh_task import NewHighTask
from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from task.background.after_market.premium_watchlist_generator_task import PremiumWatchlistGeneratorTask
from task.background.after_market.ranking_task import RankingTask
from task.background.after_market.strategy_log_report_task import StrategyLogReportTask
from task.background.always_on.notification_queue_task import NotificationQueueTask
from task.background.intraday.opening_position_reconcile_task import OpeningPositionReconcileTask
from task.background.intraday.pre_market_health_check_task import PreMarketHealthCheckTask
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class ServiceContainer:
    """`WebAppContext` 의 서비스 조립 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx

        config_dict = ctx.full_config
        if hasattr(config_dict, "model_dump"):
            config_dict = config_dict.model_dump()
        elif hasattr(config_dict, "dict"):
            config_dict = config_dict.dict()

        perf_log = config_dict.get("performance_logging", False)
        perf_threshold = config_dict.get("performance_threshold", 0.1)
        ctx.pm = PerformanceProfiler(enabled=perf_log, threshold=perf_threshold)

        try:
            cache_store = CacheStore(config_dict)
            cache_store.set_logger(ctx.logger)
            ctx.stock_repository = StockRepository(logger=ctx.logger)
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:Repository] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            ctx.rs_rating_repository = RSRatingRepository(logger=ctx.logger)
            ctx.rs_rating_service = RSRatingService(
                stock_ohlcv_repository=ctx.stock_repository._ohlcv_repo,
                rs_rating_repository=ctx.rs_rating_repository,
                stock_code_repository=ctx.stock_code_repository,
                logger=ctx.logger,
                performance_profiler=ctx.pm,
            )
        except Exception as e:
            ctx.logger.warning(f"[ServiceBootstrap:RSRating] 초기화 실패: {e}")

        try:
            ctx.market_data_service = MarketDataService(
                broker_api_wrapper=ctx.broker, env=ctx.env, logger=ctx.logger, market_clock=ctx.market_clock, cache_store=cache_store,
                market_calendar_service=ctx._mcs,
                performance_profiler=ctx.pm,
                stock_repository=ctx.stock_repository,
                data_quality_service=getattr(ctx, "data_quality_service", None),
            )
            ctx.indicator_service = IndicatorService(cache_store=cache_store, performance_profiler=ctx.pm)
            ctx.message_broker = MessageBroker()
            ctx.dlq_manager = DlqManager(logger=ctx.logger)
            ctx.worker_pool = WorkerPool(
                broker=ctx.message_broker,
                dlq_manager=ctx.dlq_manager,
                logger=ctx.logger,
                num_workers=1,  # after_market 태스크는 API 레이트 리밋 고려해 순차 실행
            )
            ctx.time_dispatcher = TimeDispatcher(
                broker=ctx.message_broker,
                market_clock=ctx.market_clock,
                mcs=ctx._mcs,
                logger=ctx.logger,
            )
            ctx.data_quality_service = DataQualityService(
                config=getattr(ctx.full_config, "data_quality", None) or DataQualityConfig(),
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                operator_alert_service=ctx.operator_alert_service,
            )
            ctx.data_quality_service.apply_trading_mode(bool(getattr(ctx.env, "is_paper_trading", True)))
            ctx.market_data_service._data_quality_service = ctx.data_quality_service
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:CoreServices] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            ctx.ranking_task = RankingTask(
                broker_api_wrapper=ctx.broker,
                stock_code_repository=ctx.stock_code_repository,
                env=ctx.env,
                logger=ctx.logger,
                market_clock=ctx.market_clock,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                market_calendar_service=ctx._mcs,
                market_data_service=ctx.market_data_service,
                worker_pool=ctx.worker_pool,
            )
            ctx.stock_query_service = StockQueryService(
                market_data_service=ctx.market_data_service, logger=ctx.logger, market_clock=ctx.market_clock,
                indicator_service=ctx.indicator_service,
                ranking_task=ctx.ranking_task,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                broker_api_wrapper=ctx.broker,
                streaming_logger=ctx.streaming_event_logger,
            )
            # IndicatorService에 StockQueryService 주입 (순환 참조 해결)
            ctx.indicator_service.stock_query_service = ctx.stock_query_service
            ctx.favorite_service.stock_query_service = ctx.stock_query_service
            ctx.favorite_service.stock_repository = ctx.stock_repository
            ctx.favorite_service.rs_rating_service = getattr(ctx, "rs_rating_service", None)
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:QueryServices] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            ctx.minervini_stage_service = MinerviniStageService(
                stock_query_service=ctx.stock_query_service,
                rs_rating_service=getattr(ctx, "rs_rating_service", None),
                stock_repository=ctx.stock_repository,
                logger=ctx.logger,
            )
            ctx.favorite_service.minervini_stage_service = ctx.minervini_stage_service
        except Exception as e:
            ctx.logger.warning(f"[ServiceBootstrap:MinerviniStage] 초기화 실패: {e}")
            ctx.minervini_stage_service = None

        try:
            ctx.minervini_update_task = MinerviniUpdateTask(
                minervini_service=getattr(ctx, 'minervini_stage_service', None),
                stock_code_repository=ctx.stock_code_repository,
                stock_repository=ctx.stock_repository,
                stock_query_service=ctx.stock_query_service,
                broker_api_wrapper=ctx.broker,
                rs_rating_service=getattr(ctx, 'rs_rating_service', None),
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                market_calendar_service=ctx._mcs,
                worker_pool=ctx.worker_pool,
            )
        except Exception as e:
            ctx.logger.warning(f"[ServiceBootstrap:MinerviniUpdate] 초기화 실패: {e}")
            ctx.minervini_update_task = None

        # MinerviniStageService에 MinerviniUpdateTask 연결 (생성 순서 때문에 사후 주입)
        if ctx.minervini_stage_service and ctx.minervini_update_task:
            ctx.minervini_stage_service._minervini_update_task = ctx.minervini_update_task

        try:
            ctx.streaming_service = StreamingService(
                broker_api_wrapper=ctx.broker,
                logger=ctx.logger,
                market_clock=ctx.market_clock,
                market_data_service=ctx.market_data_service,
                streaming_logger=ctx.streaming_event_logger,
                data_quality_service=ctx.data_quality_service,
            )
            ctx.price_stream_service = PriceStreamService(
                stock_repo=ctx.stock_repository,
                logger=ctx.logger,
                data_quality_service=ctx.data_quality_service,
                notification_service=ctx.notification_service,
            )
            ctx.data_quality_service.set_price_stream_service(ctx.price_stream_service)
            ctx.streaming_service.set_price_stream_service(ctx.price_stream_service)
            if ctx.stock_query_service:
                ctx.stock_query_service.price_stream_service = ctx.price_stream_service
            ctx.streaming_stock_repo = StreamingStockRepo(logger=ctx.logger)
            ctx.streaming_stock_repo.load_pt_desired_from_db("data/program_subscribe/program_trading.db")
            ctx.streaming_service.set_streaming_stock_repo(ctx.streaming_stock_repo)
            ctx.program_trading_stream_service.wire_streaming_stock_repo(ctx.streaming_stock_repo)
            ctx.price_subscription_service = PriceSubscriptionService(
                streaming_service=ctx.streaming_service,
                stock_repo=ctx.stock_repository,
                logger=ctx.logger,
                streaming_logger=ctx.streaming_event_logger,
                streaming_stock_repo=ctx.streaming_stock_repo,
                market_calendar=ctx._mcs,
            )
            if ctx.stock_query_service:
                ctx.stock_query_service.price_subscription_service = ctx.price_subscription_service
            ctx.websocket_watchdog_task = WebSocketWatchdogTask(
                streaming_service=ctx.streaming_service,
                program_trading_stream_service=ctx.program_trading_stream_service,
                market_calendar_service=ctx._mcs,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                operator_alert_service=ctx.operator_alert_service,
                logger=ctx.logger,
                streaming_logger=ctx.streaming_event_logger,
                streaming_stock_repo=ctx.streaming_stock_repo,
                price_subscription_service=ctx.price_subscription_service,
                price_stream_service=ctx.price_stream_service,
            )
            ctx.pre_market_health_check_task = PreMarketHealthCheckTask(
                broker=ctx.broker,
                env=ctx.env,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                streaming_stock_repo=ctx.streaming_stock_repo,
                data_quality_service=ctx.data_quality_service,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
            )
            ctx.daily_price_collector_task = DailyPriceCollectorTask(
                stock_query_service=ctx.stock_query_service,
                stock_code_repository=ctx.stock_code_repository,
                stock_repo=ctx.stock_repository,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
                rs_rating_service=getattr(ctx, "rs_rating_service", None),
                worker_pool=ctx.worker_pool,
            )
            # NOTE: MinerviniUpdateTask._daily_price_collector_task는 DailyPriceCollectorTask 생성 후 사후 주입
            if ctx.minervini_update_task and ctx.daily_price_collector_task:
                ctx.minervini_update_task._daily_price_collector_task = ctx.daily_price_collector_task
            ctx.ohlcv_update_task = OhlcvUpdateTask(
                stock_query_service=ctx.stock_query_service,
                stock_code_repository=ctx.stock_code_repository,
                stock_repo=ctx.stock_repository,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
            )
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:Streaming] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            _ps_cfg = getattr(ctx.full_config, "position_sizing", None) or PositionSizingConfig()
            _rg_cfg = getattr(ctx.full_config, "risk_gate", None) or RiskGateConfig()
            _op_cfg = getattr(ctx.full_config, "order_policy", None) or OrderPolicyConfig()
            ctx.account_snapshot_cache = AccountSnapshotCache(
                broker_api_wrapper=ctx.broker,
                logger=ctx.logger,
                ttl_sec=_ps_cfg.snapshot_ttl_sec if _ps_cfg else 60,
            )
            ctx.position_sizing_service = PositionSizingService(
                account_snapshot_cache=ctx.account_snapshot_cache,
                indicator_service=ctx.indicator_service,
                config=_ps_cfg,
                logger=ctx.logger,
                risk_gate_config=_rg_cfg,
                quote_provider=ctx.broker,
                order_policy_config=_op_cfg,
            )
            ctx.risk_gate_service = RiskGateService(
                config=_rg_cfg,
                kill_switch_service=ctx.kill_switch_service,
                account_snapshot_cache=ctx.account_snapshot_cache,
                strategy_risk_provider=ctx.virtual_trade_service,
                logger=ctx.logger,
                env=getattr(ctx.broker, "env", None),
                operator_alert_service=ctx.operator_alert_service,
            )
            ctx.execution_flow_service = ExecutionFlowService(
                data_provider=ctx.broker,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                cache_ttl_sec=_op_cfg.trade_flow_cache_ttl_sec,
                sample_window_sec=_op_cfg.trade_flow_sample_window_sec,
                stock_query_service=ctx.stock_query_service,
            )
            ctx.order_policy_service = OrderPolicyService(
                config=_op_cfg,
                quote_provider=ctx.broker,
                security_info_provider=ctx.broker,
                trade_flow_provider=ctx.execution_flow_service,
                logger=ctx.logger,
            )
            ctx.deferred_order_queue = DeferredOrderQueue(ctx.logger)
            ctx.order_execution_service = OrderExecutionService(
                broker_api_wrapper=ctx.broker,
                logger=ctx.logger, market_clock=ctx.market_clock,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                market_calendar_service=ctx._mcs,
                price_subscription_service=ctx.price_subscription_service,
                virtual_trade_service=ctx.virtual_trade_service,
                kill_switch_service=ctx.kill_switch_service,
                account_snapshot_cache=ctx.account_snapshot_cache,
                risk_gate_service=ctx.risk_gate_service,
                order_policy_service=ctx.order_policy_service,
                data_quality_service=ctx.data_quality_service,
                execution_quality_config=getattr(ctx.full_config, "execution_quality_report", None),
                deferred_order_queue=ctx.deferred_order_queue,
            )
            ctx.streaming_service.register_handler(
                "signing_notice",
                ctx.order_execution_service.handle_signing_notice,
            )
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:OrderServices] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            ctx.oneil_universe_service = OneilUniverseService(
                stock_query_service=ctx.stock_query_service,
                indicator_service=ctx.indicator_service,
                stock_code_repository=ctx.stock_code_repository,
                market_clock=ctx.market_clock,
                scraper_service=NaverFinanceScraperService(logger=ctx.logger),
                logger=ctx.logger,
                performance_profiler=ctx.pm,
                price_subscription_service=ctx.price_subscription_service,
                rs_rating_service=getattr(ctx, "rs_rating_service", None),
                minervini_service=getattr(ctx, "minervini_stage_service", None),
                notification_service=getattr(ctx, "notification_service", None),
            )
            ctx.premium_watchlist_generator_task = PremiumWatchlistGeneratorTask(
                universe_service=ctx.oneil_universe_service,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
            )
            ctx.cache_warmup_task = CacheWarmupTask(
                market_data_service=ctx.market_data_service,
                stock_query_service=ctx.stock_query_service,
                universe_service=ctx.oneil_universe_service,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
            )
            ctx.log_cleanup_task = LogCleanupTask(
                log_dir=ctx.logger.log_dir,
                delete_days=30,
                compress_days=7,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
            )
            ctx.newhigh_task = NewHighTask(
                stock_repo=ctx.stock_repository,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                notification_service=ctx.notification_service,
                daily_price_collector_task=ctx.daily_price_collector_task,
                stock_query_service=ctx.stock_query_service,
                rs_rating_service=getattr(ctx, 'rs_rating_service', None),
                worker_pool=ctx.worker_pool,
            )
            ctx.newhigh_service = NewHighService(
                stock_repository=ctx.stock_repository,
                newhigh_task=ctx.newhigh_task,
                logger=ctx.logger,
            )
            ctx.strategy_log_report_task = StrategyLogReportTask(
                report_service=StrategyLogReportService(
                    log_dir=os.path.join(ctx.logger.log_dir, "strategies"),
                    stock_code_repo=ctx.stock_code_repository,
                    virtual_trade_service=ctx.virtual_trade_service,
                    backtest_journal_provider=ctx.backtest_journal_repository.load_records_for_date,
                    execution_quality_config=getattr(ctx.full_config, "execution_quality_report", None),
                    strategy_degradation_config=getattr(
                        ctx.full_config, "strategy_performance_degradation", None
                    ),
                    enabled_strategy_provider=ctx._get_enabled_strategy_names_for_report,
                ),
                notification_service=ctx.notification_service,
                operator_alert_service=ctx.operator_alert_service,
                kill_switch_service=ctx.kill_switch_service,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                mcs=ctx._mcs,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
                rejection_distribution_service=ctx.rejection_distribution_service,
            )
            ctx.notification_queue_task = NotificationQueueTask(
                notification_service=ctx.notification_service,
                poll_interval=config_dict.get("notification_queue_poll_interval", 1.0),
                telegram_config=getattr(getattr(ctx.full_config, "notifications", None), "telegram", None),
                logger=ctx.logger,
            )
            ctx.after_market_reconcile_task = AfterMarketReconcileTask(
                order_execution_service=ctx.order_execution_service,
                notification_service=ctx.notification_service,
                operator_alert_service=ctx.operator_alert_service,
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                worker_pool=ctx.worker_pool,
            )
            reconcile_cfg = getattr(ctx.full_config, "opening_position_reconcile", None)
            ctx.opening_position_reconcile_task = None
            if reconcile_cfg is None or getattr(reconcile_cfg, "enabled", True):
                deprecated_reconcile_keys = (
                    "detect_only",
                    "auto_buy_missing_local",
                    "auto_sell_extra_broker",
                    "allow_sell_unknown_broker",
                )
                reconcile_extra = getattr(reconcile_cfg, "model_extra", {}) or {}
                for key in deprecated_reconcile_keys:
                    if key in reconcile_extra:
                        ctx.logger.warning(
                            f"opening_position_reconcile.{key} is deprecated and ignored"
                        )
                opening_reconcile_service = OpeningPositionReconcileService(
                    broker=ctx.broker,
                    virtual_trade_service=ctx.virtual_trade_service,
                    logger=ctx.logger,
                )
                ctx.opening_position_reconcile_task = OpeningPositionReconcileTask(
                    reconcile_service=opening_reconcile_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    notification_service=ctx.notification_service,
                    operator_alert_service=ctx.operator_alert_service,
                    logger=ctx.logger,
                    check_interval_sec=getattr(reconcile_cfg, "check_interval_sec", 30),
                    open_delay_sec=getattr(reconcile_cfg, "open_delay_sec", 60),
                    run_window_min=getattr(reconcile_cfg, "run_window_min", 10),
                )
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:Universe] 초기화 실패: {e}", exc_info=True)
            raise
