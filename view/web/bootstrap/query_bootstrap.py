"""랭킹·시가총액 갭 리포트와 주식 조회 서비스 조립."""

from typing import Any, TYPE_CHECKING

from core.market_clock import MarketClock
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from services.market_cap_gap_service import MarketCapGapService
from services.stock_query_service import StockQueryService
from task.background.after_market.market_cap_gap_report_task import MarketCapGapReportTask
from task.background.after_market.ranking_task import RankingTask
from task.background.after_market.ytd_ranking_report_task import YtdRankingReportTask

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class QueryBootstrap:
    """조회 서비스와 조회 기반 배치 태스크를 컨텍스트에 구성한다."""

    def __init__(self, context: "WebAppContext", us_market_calendar_factory) -> None:
        self._ctx = context
        self._us_market_calendar_factory = us_market_calendar_factory

    def run(
        self,
        *,
        config: dict[str, Any],
        is_overseas_us: bool,
        needs_batch: bool,
    ) -> None:
        ctx = self._ctx
        try:
            if is_overseas_us:
                ctx.ranking_task = None
                ctx.market_cap_gap_service = None
                ctx.market_cap_gap_report_kr_task = None
                ctx.market_cap_gap_report_us_task = None
                ctx.ytd_ranking_report_task = None
            else:
                ctx.ranking_task = RankingTask(
                    broker_api_wrapper=ctx.broker,
                    stock_code_repository=ctx.stock_code_repository,
                    env=ctx.env,
                    logger=ctx.logger,
                    market_clock=ctx.market_clock,
                    performance_profiler=ctx.pm,
                    notification_service=ctx.notification_service,
                    telegram_reporter=getattr(ctx, "telegram_reporter", None),
                    market_calendar_service=ctx._mcs,
                    market_data_service=ctx.market_data_service,
                    worker_pool=ctx.worker_pool,
                    stock_classification_repository=getattr(
                        ctx,
                        "theme_classification_repository",
                        None,
                    ),
                )
                self._build_market_cap_gap_tasks(config, needs_batch)

            ctx.stock_query_service = StockQueryService(
                market_data_service=ctx.market_data_service,
                logger=ctx.logger,
                market_clock=ctx.market_clock,
                indicator_service=ctx.indicator_service,
                ranking_task=ctx.ranking_task,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                broker_api_wrapper=ctx.broker,
                streaming_logger=ctx.streaming_event_logger,
            )
        except Exception as exc:
            ctx.logger.critical(
                f"[ServiceBootstrap:QueryServices] 초기화 실패: {exc}",
                exc_info=True,
            )
            raise

    def _build_market_cap_gap_tasks(self, config: dict[str, Any], needs_batch: bool) -> None:
        ctx = self._ctx
        if not needs_batch:
            ctx.market_cap_gap_service = None
            ctx.market_cap_gap_report_kr_task = None
            ctx.market_cap_gap_report_us_task = None
            ctx.ytd_ranking_report_task = None
            return

        ctx.market_cap_gap_service = MarketCapGapService.build_default(
            broker=ctx.broker,
            logger=ctx.logger,
        )
        kr_gap_enabled = config.get("market_cap_gap_report_kr_enabled", True)
        us_gap_enabled = config.get("market_cap_gap_report_us_enabled", True)
        gap_report_store = StrategySchedulerStore(logger=ctx.logger)
        ytd_report_enabled = config.get("ytd_ranking_report_enabled", True)
        ctx.ytd_ranking_report_task = YtdRankingReportTask(
            stock_repository=ctx.stock_repository,
            telegram_reporter=getattr(ctx, "telegram_reporter", None),
            market_calendar_service=ctx._mcs,
            market_clock=ctx.market_clock,
            scheduler_store=gap_report_store,
            worker_pool=ctx.worker_pool,
            logger=ctx.logger,
        ) if ytd_report_enabled else None
        ctx.market_cap_gap_report_kr_task = MarketCapGapReportTask(
            market_cap_gap_service=ctx.market_cap_gap_service,
            telegram_reporter=getattr(ctx, "telegram_reporter", None),
            notification_service=ctx.notification_service,
            session="kr_close",
            market_calendar_service=ctx._mcs,
            market_clock=ctx.market_clock,
            scheduler_store=gap_report_store,
            logger=ctx.logger,
        ) if kr_gap_enabled else None

        us_gap_clock = MarketClock.for_us_equities(logger=ctx.logger)
        ctx.market_cap_gap_report_us_task = MarketCapGapReportTask(
            market_cap_gap_service=ctx.market_cap_gap_service,
            telegram_reporter=getattr(ctx, "telegram_reporter", None),
            notification_service=ctx.notification_service,
            session="us_close",
            market_calendar_service=self._us_market_calendar_factory(
                market_clock=us_gap_clock,
                logger=ctx.logger,
            ),
            market_clock=us_gap_clock,
            scheduler_store=gap_report_store,
            logger=ctx.logger,
        ) if us_gap_enabled else None
