"""실시간 스트리밍, 가격 구독 및 watchdog 조립."""

import calendar
from datetime import date, datetime
from typing import Any, TYPE_CHECKING

from repositories.execution_strength_repo import ExecutionStrengthRepository
from repositories.orderbook_snapshot_repo import OrderbookSnapshotRepository
from repositories.streaming_stock_repo import StreamingStockRepo
from services.event_shadow_journal_service import EventShadowJournalService
from services.favorite_price_alert_service import FavoritePriceAlertService
from services.price_stream_service import PriceStreamService
from services.price_subscription_service import PriceSubscriptionService
from services.strategy_event_router import StrategyEventRouter
from services.streaming_service import StreamingService
from services.market_status_alert_service import MarketStatusAlertService
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

        market_status_cfg = config.get("market_status_alert", {}) if isinstance(config, dict) else {}
        market_status_enabled = (
            market_status_cfg.get("enabled", True)
            if isinstance(market_status_cfg, dict)
            else True
        )
        configured_monitor_codes = (
            market_status_cfg.get("monitor_codes", ["005930"])
            if isinstance(market_status_cfg, dict)
            else ["005930"]
        )
        monitor_codes = [
            str(code).strip()
            for code in (configured_monitor_codes or [])
            if str(code).strip().isdigit()
            and len(str(code).strip()) == 6
            and str(code).strip() != "000000"
        ] or ["005930"]
        configured_futures_codes = (
            market_status_cfg.get("futures_monitor_codes", [])
            if isinstance(market_status_cfg, dict)
            else []
        )
        futures_monitor_codes = [
            str(code).strip() for code in (configured_futures_codes or []) if str(code).strip()
        ]
        if not futures_monitor_codes:
            default_futures_code = self._default_kospi200_futures_code(datetime.now().date())
            futures_monitor_codes = [default_futures_code] if default_futures_code else []
        ctx.market_status_alert_service = (
            MarketStatusAlertService(
                operator_alert_service=ctx.operator_alert_service,
                notification_service=ctx.notification_service,
                logger=ctx.logger,
            )
            if market_status_enabled
            else None
        )
        ctx.streaming_service = StreamingService(
            broker_api_wrapper=ctx.broker,
            logger=ctx.logger,
            market_clock=ctx.market_clock,
            market_data_service=ctx.market_data_service,
            streaming_logger=ctx.streaming_event_logger,
            data_quality_service=ctx.data_quality_service,
            market_status_alert_service=ctx.market_status_alert_service,
            market_status_monitor_codes=monitor_codes if market_status_enabled else [],
            futures_sidecar_monitor_codes=futures_monitor_codes if market_status_enabled else [],
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
        orderbook_capture_enabled = config.get("orderbook_capture_enabled", True)
        ctx.orderbook_snapshot_repo = (
            OrderbookSnapshotRepository(logger=ctx.logger)
            if orderbook_capture_enabled
            else None
        )
        ctx.favorite_price_alert_service = FavoritePriceAlertService(
            favorite_repository=ctx.favorite_repo,
            notification_service=ctx.notification_service,
            stock_code_repository=ctx.stock_code_repository,
            state_file="data/favorite_price_alert_state.json",
            logger=ctx.logger,
        )
        ctx.price_stream_service = PriceStreamService(
            stock_repo=ctx.stock_repository,
            logger=ctx.logger,
            data_quality_service=ctx.data_quality_service,
            notification_service=ctx.notification_service,
            favorite_price_alert_service=ctx.favorite_price_alert_service,
            event_router=ctx.strategy_event_router,
            execution_strength_recorder=ctx.execution_strength_repo,
            orderbook_recorder=ctx.orderbook_snapshot_repo,
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
        ctx.orderbook_snapshot_repo = None
        ctx.price_stream_service = None
        ctx.favorite_price_alert_service = None
        ctx.streaming_stock_repo = None
        ctx.price_subscription_service = None
        ctx.websocket_watchdog_task = None
        ctx.market_status_alert_service = None

    @staticmethod
    def _default_kospi200_futures_code(as_of: date) -> str | None:
        """빈 설정일 때 사용할 코스피200 최근월물 단축코드를 계산한다."""
        contract_year = as_of.year
        contract_month = next((month for month in (3, 6, 9, 12) if month >= as_of.month), 3)

        if as_of.month == contract_month:
            thursdays = [
                week[calendar.THURSDAY]
                for week in calendar.monthcalendar(contract_year, contract_month)
                if week[calendar.THURSDAY]
            ]
            expiry = date(contract_year, contract_month, thursdays[1])
            if as_of > expiry:
                if contract_month == 12:
                    contract_year += 1
                    contract_month = 3
                else:
                    contract_month += 3

        year_offset = contract_year - 2010
        if not 0 <= year_offset <= 99:
            return None
        return f"A0{year_offset:02d}{contract_month:02d}"
