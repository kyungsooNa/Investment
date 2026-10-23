"""코스피/코스닥 투자자 수급 시간차트(표본 수집 서비스 + 장중 수집 태스크) 조립."""
from __future__ import annotations

from services.index_flow_history_service import IndexFlowHistoryService
from task.background.intraday.index_flow_sampler_task import IndexFlowSamplerTask


def build_index_flow(ctx, *, needs_trading: bool) -> None:
    # 조회(차트)는 저장된 표본만 읽으므로 서비스는 항상 두고, 수집 태스크만 매매 모드에 묶는다.
    ctx.index_flow_history_service = IndexFlowHistoryService(
        stock_query_service=ctx.stock_query_service,
        market_clock=ctx.market_clock,
        logger=ctx.logger,
    ) if ctx.stock_query_service is not None else None
    ctx.index_flow_sampler_task = IndexFlowSamplerTask(
        index_flow_history_service=ctx.index_flow_history_service,
        market_clock=ctx.market_clock,
        market_calendar_service=ctx._mcs,
        logger=ctx.logger,
    ) if needs_trading and ctx.index_flow_history_service is not None else None
