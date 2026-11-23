"""장전 양·음봉/전일 대비 방향 예측 서비스와 알림·장마감 채점 태스크 조립."""
from __future__ import annotations

from services.premarket_direction_service import DailyHistoryFetcher, PremarketDirectionService
from task.background.after_market.premarket_direction_result_task import PremarketDirectionResultTask
from task.background.intraday.premarket_direction_forecast_task import PremarketDirectionForecastTask


def build_premarket_direction(ctx, *, needs_trading: bool, needs_batch: bool) -> None:
    # 웹 카드가 서비스를 직접 조회하므로 서비스는 항상 두고, 장전 알림 태스크만 매매 모드에 묶는다.
    ctx.premarket_direction_service = PremarketDirectionService(
        fetcher=DailyHistoryFetcher(),
        history_path="data/premarket_direction/history.json",
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
    # 장 마감 후 예측을 확정 종가로 채점해 결과·누적 성적을 알린다(다른 장마감 배치와 같은 모드).
    ctx.premarket_direction_result_task = PremarketDirectionResultTask(
        premarket_direction_service=ctx.premarket_direction_service,
        mcs=ctx._mcs,
        market_clock=ctx.market_clock,
        logger=ctx.logger,
        worker_pool=ctx.worker_pool,
    ) if needs_batch else None
