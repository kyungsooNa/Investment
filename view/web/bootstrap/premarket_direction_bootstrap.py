"""장전 시가 갭 예측(정보 표시 전용, 매매 게이트 미연결) 서비스 + 장전 알림 태스크 조립."""
from __future__ import annotations

from services.premarket_direction_service import DailyHistoryFetcher, PremarketDirectionService
from task.background.intraday.premarket_direction_forecast_task import PremarketDirectionForecastTask


def build_premarket_direction(ctx, *, needs_trading: bool) -> None:
    # 웹 카드가 서비스를 직접 조회하므로 서비스는 항상 두고, 장전 알림 태스크만 매매 모드에 묶는다.
    ctx.premarket_direction_service = PremarketDirectionService(
        fetcher=DailyHistoryFetcher(),
        market_clock=ctx.market_clock,
        notification_service=ctx.notification_service,
        logger=ctx.logger,
    )
    ctx.premarket_direction_forecast_task = PremarketDirectionForecastTask(
        premarket_direction_service=ctx.premarket_direction_service,
        market_clock=ctx.market_clock,
        market_calendar_service=ctx._mcs,
        logger=ctx.logger,
    ) if needs_trading else None
