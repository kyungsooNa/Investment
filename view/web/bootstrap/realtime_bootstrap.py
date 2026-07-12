"""실시간 스트리밍, 가격 구독 및 watchdog 조립."""

from typing import Any, TYPE_CHECKING

from repositories.execution_strength_repo import ExecutionStrengthRepository
from repositories.streaming_stock_repo import StreamingStockRepo
from services.event_shadow_journal_service import EventShadowJournalService
from services.price_stream_service import PriceStreamService
from services.price_subscription_service import PriceSubscriptionService
from services.strategy_event_router import StrategyEventRouter
from services.streaming_service import StreamingService
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class RealtimeBootstrap:
    """실시간 서비스 체인을 컨텍스트에 구성하거나 명시적으로 비활성화한다."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self, *, config: dict[str, Any], needs_realtime: bool) -> None:
        ctx = self._ctx
        if not needs_realtime:
            self._disable()
            return

        ctx.streaming_service = StreamingService(
            broker_api_wrapper=ctx.broker,
            logger=ctx.logger,
            market_clock=ctx.market_clock,
            market_data_service=ctx.market_data_service,
            streaming_logger=ctx.streaming_event_logger,
            data_quality_service=ctx.data_quality_service,
        )
        ctx.event_shadow_journal_service = EventShadowJournalService(
            log_root="logs/strategies",
            logger=ctx.logger,
        )
        ctx.strategy_event_router = StrategyEventRouter(
            market_clock=ctx._mcs,
            kill_switch_service=ctx.kill_switch_service,
            logger=ctx.logger,
            throttle_sec=0.1,
            signal_debounce_sec=0.5,
            signal_sink=None,
        )
        es_capture_enabled = config.get("execution_strength_capture_enabled", True)
        ctx.execution_strength_repo = (
            ExecutionStrengthRepository(logger=ctx.logger)
            if es_capture_enabled
            else None
        )
        ctx.price_stream_service = PriceStreamService(
            stock_repo=ctx.stock_repository,
            logger=ctx.logger,
            data_quality_service=ctx.data_quality_service,
            notification_service=ctx.notification_service,
            event_router=ctx.strategy_event_router,
            execution_strength_recorder=ctx.execution_strength_repo,
        )
        ctx.streaming_stock_repo = StreamingStockRepo(logger=ctx.logger)
        snapshot = ctx.program_trading_stream_service.load_snapshot()
        fallback_codes = []
        if isinstance(snapshot, dict):
            raw_codes = snapshot.get("subscribedCodes", [])
            if isinstance(raw_codes, list):
                fallback_codes = raw_codes
        ctx.streaming_stock_repo.load_pt_desired_from_db(
            "data/program_subscribe/program_trading.db",
            fallback_codes=fallback_codes,
        )
        ctx.price_subscription_service = PriceSubscriptionService(
            streaming_service=ctx.streaming_service,
            stock_repo=ctx.stock_repository,
            logger=ctx.logger,
            streaming_logger=ctx.streaming_event_logger,
            streaming_stock_repo=ctx.streaming_stock_repo,
            market_calendar=ctx._mcs,
        )
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

    def _disable(self) -> None:
        ctx = self._ctx
        ctx.streaming_service = None
        ctx.event_shadow_journal_service = None
        ctx.strategy_event_router = None
        ctx.execution_strength_repo = None
        ctx.price_stream_service = None
        ctx.streaming_stock_repo = None
        ctx.price_subscription_service = None
        ctx.websocket_watchdog_task = None
