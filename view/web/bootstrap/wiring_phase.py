"""WiringPhase — `ServiceContainer` 가 인스턴스화한 서비스 간 후주입을 한곳에 모은다.

`ServiceContainer` 는 인스턴스 생성만 책임지고, 모든 상호 참조 / 양방향
의존 / 콜백 등록은 본 모듈에서 수행한다. 후주입 누락 시 단위 테스트가
즉시 실패하도록 contract 를 명확히 한다.

진성 순환 의존(예: DataQualityService ↔ PriceStreamService)은 그대로
유지하되, 모든 wire 가 본 파일 한곳에 모인다는 점이 핵심이다. 후주입
완전 제거(생성자 주입 전환)는 별도 PR 에서 진행한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class WiringPhase:
    """`ServiceContainer.run()` 직후 호출되어 서비스 간 후주입을 일괄 수행한다."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx

        # MarketDataService ← DataQualityService (CoreServices 단계 backfill)
        ctx.market_data_service.set_data_quality_service(ctx.data_quality_service)

        # IndicatorService ↔ StockQueryService (순환 참조 해결)
        ctx.indicator_service.stock_query_service = ctx.stock_query_service

        # FavoriteService 의존성 묶음 주입
        ctx.favorite_service.stock_query_service = ctx.stock_query_service
        ctx.favorite_service.stock_repository = ctx.stock_repository
        ctx.favorite_service.rs_rating_service = getattr(ctx, "rs_rating_service", None)
        ctx.favorite_service.minervini_stage_service = getattr(
            ctx, "minervini_stage_service", None
        )

        # MinerviniStage ↔ MinerviniUpdate (양방향, 둘 다 존재할 때만)
        if ctx.minervini_stage_service and ctx.minervini_update_task:
            ctx.minervini_stage_service.set_minervini_update_task(ctx.minervini_update_task)

        # MinerviniUpdate ← DailyPriceCollector (생성 순서 backfill)
        if ctx.minervini_update_task and ctx.daily_price_collector_task:
            ctx.minervini_update_task.set_daily_price_collector_task(ctx.daily_price_collector_task)

        # DataQuality ↔ PriceStream (진성 순환). BATCH 단독은 realtime chain 을 만들지 않는다.
        if ctx.price_stream_service:
            ctx.data_quality_service.set_price_stream_service(ctx.price_stream_service)

        # Streaming ← PriceStream (생성 순서 backfill)
        if ctx.streaming_service and ctx.price_stream_service:
            ctx.streaming_service.set_price_stream_service(ctx.price_stream_service)

        # StockQuery ← PriceStream / PriceSubscription (snapshot-first 경로)
        if ctx.stock_query_service:
            ctx.stock_query_service.price_stream_service = ctx.price_stream_service
            ctx.stock_query_service.price_subscription_service = ctx.price_subscription_service

        # Streaming ← StreamingStockRepo
        if ctx.streaming_service and ctx.streaming_stock_repo:
            ctx.streaming_service.set_streaming_stock_repo(ctx.streaming_stock_repo)

        # ProgramTrading ← StreamingStockRepo
        if ctx.streaming_stock_repo:
            ctx.program_trading_stream_service.wire_streaming_stock_repo(ctx.streaming_stock_repo)

        # ProgramTrading 운영 알림 ← TelegramReporter / MarketCalendar / MarketClock
        if ctx.program_trading_stream_service:
            ctx.program_trading_stream_service.wire_alert_dependencies(
                telegram_reporter=getattr(ctx, "telegram_reporter", None),
                market_calendar_service=getattr(ctx, "_mcs", None),
                market_clock=getattr(ctx, "market_clock", None),
                stock_code_repository=getattr(ctx, "stock_code_repository", None),
            )

        # Streaming realtime_program_trading callback ← ProgramTradingStreamService
        if ctx.streaming_service and ctx.program_trading_stream_service:
            ctx.streaming_service.register_handler(
                "realtime_program_trading",
                ctx.program_trading_stream_service.on_data_received,
            )

        # Streaming signing_notice callback ← OrderExecution
        if ctx.streaming_service and ctx.order_execution_service:
            ctx.streaming_service.register_handler(
                "signing_notice",
                ctx.order_execution_service.handle_signing_notice,
            )
