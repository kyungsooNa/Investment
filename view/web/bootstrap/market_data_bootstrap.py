"""시장 데이터 핵심 서비스와 공용 작업 인프라 조립."""

from typing import TYPE_CHECKING

from config.config_loader import DataQualityConfig
from core.market_clock import MarketClock
from repositories.rs_rating_repository import RSRatingRepository
from repositories.stock_classification_repository import StockClassificationRepository
from scheduler.dispatcher.time_dispatcher import TimeDispatcher
from scheduler.ticket_queue.dlq_manager import DlqManager
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.worker.worker_pool import WorkerPool
from services.data_quality_service import DataQualityService
from services.indicator_service import IndicatorService
from services.market_data_service import MarketDataService
from services.rs_rating_service import RSRatingService
from services.theme_daily_leader_service import ThemeDailyLeaderService
from services.theme_leader_service import ThemeLeaderService
from view.web.market_mode_utils import is_market_enabled

if TYPE_CHECKING:  # pragma: no cover
    from core.cache.cache_store import CacheStore
    from view.web.web_app_initializer import WebAppContext


class MarketDataBootstrap:
    """시장 데이터 핵심 서비스와 공용 dispatcher를 컨텍스트에 구성한다."""

    def __init__(self, context: "WebAppContext", us_market_calendar_factory) -> None:
        self._ctx = context
        self._us_market_calendar_factory = us_market_calendar_factory

    def run(self, cache_store: "CacheStore") -> None:
        ctx = self._ctx
        try:
            ctx.rs_rating_repository = RSRatingRepository(logger=ctx.logger)
            ctx.rs_rating_service = RSRatingService(
                stock_ohlcv_repository=ctx.stock_repository._ohlcv_repo,
                rs_rating_repository=ctx.rs_rating_repository,
                stock_code_repository=ctx.stock_code_repository,
                logger=ctx.logger,
                performance_profiler=ctx.pm,
            )
        except Exception as exc:
            ctx.logger.warning(f"[ServiceBootstrap:RSRating] 초기화 실패: {exc}")

        try:
            ctx.theme_classification_repository = StockClassificationRepository(logger=ctx.logger)
            ctx.theme_leader_service = ThemeLeaderService(
                classification_repository=ctx.theme_classification_repository,
                rs_rating_repository=getattr(ctx, "rs_rating_repository", None),
                logger=ctx.logger,
                performance_profiler=ctx.pm,
            )
            ctx.theme_daily_leader_service = ThemeDailyLeaderService(
                classification_repository=ctx.theme_classification_repository,
                logger=ctx.logger,
                performance_profiler=ctx.pm,
            )
        except Exception as exc:
            ctx.logger.warning(f"[ServiceBootstrap:ThemeLeader] 초기화 실패: {exc}")
            ctx.theme_classification_repository = None
            ctx.theme_leader_service = None
            ctx.theme_daily_leader_service = None

        try:
            ctx.market_data_service = MarketDataService(
                broker_api_wrapper=ctx.broker,
                env=ctx.env,
                logger=ctx.logger,
                market_clock=ctx.market_clock,
                cache_store=cache_store,
                market_calendar_service=ctx._mcs,
                performance_profiler=ctx.pm,
                stock_repository=ctx.stock_repository,
                data_quality_service=getattr(ctx, "data_quality_service", None),
            )
            ctx.indicator_service = IndicatorService(
                cache_store=cache_store,
                performance_profiler=ctx.pm,
                operator_alert_service=getattr(ctx, "operator_alert_service", None),
            )
            ctx.message_broker = MessageBroker()
            ctx.dlq_manager = DlqManager(logger=ctx.logger)
            ctx.worker_pool = WorkerPool(
                broker=ctx.message_broker,
                dlq_manager=ctx.dlq_manager,
                logger=ctx.logger,
                num_workers=1,
                performance_profiler=ctx.pm,
            )
            ctx.time_dispatcher = TimeDispatcher(
                broker=ctx.message_broker,
                market_clock=ctx.market_clock,
                mcs=ctx._mcs,
                logger=ctx.logger,
            )
            ctx.time_dispatcher_us = None
            if is_market_enabled(ctx, "overseas_us"):
                us_clock = MarketClock.for_us_equities(logger=ctx.logger)
                ctx.time_dispatcher_us = TimeDispatcher(
                    broker=ctx.message_broker,
                    market_clock=us_clock,
                    mcs=self._us_market_calendar_factory(
                        market_clock=us_clock,
                        logger=ctx.logger,
                    ),
                    logger=ctx.logger,
                    db_path="data/time_dispatcher_state_us.db",
                )
            ctx.data_quality_service = DataQualityService(
                config=getattr(ctx.full_config, "data_quality", None) or DataQualityConfig(),
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                operator_alert_service=ctx.operator_alert_service,
            )
            ctx.data_quality_service.apply_trading_mode(
                bool(getattr(ctx.env, "is_paper_trading", True))
            )
        except Exception as exc:
            ctx.logger.critical(
                f"[ServiceBootstrap:CoreServices] 초기화 실패: {exc}",
                exc_info=True,
            )
            raise
