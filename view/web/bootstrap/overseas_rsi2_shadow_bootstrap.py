"""미국장 RSI2 국면 게이트 비교용 Paper 관찰 경로 조립."""

from services.overseas_intraday_rsi2_service import OverseasIntradayRSI2ShadowService
from services.overseas_order_execution_service import OverseasOrderExecutionService


def build_rsi2_shadow(ctx, cfg, primary, common, calendar, journal):
    # 기존 Paper 주문·원장·알림과 분리해 별도 출처의 would-be 거래만 기록한다.
    orders = OverseasOrderExecutionService(
        broker=None, live_enabled=False, journal=journal,
        trade_repository=None, notification_service=None,
        paper_signal_source="overseas_rsi2_no_regime_paper", logger=ctx.logger,
    )
    return OverseasIntradayRSI2ShadowService(
        primary_service=primary,
        candidate_service=ctx.overseas_intraday_candidate_service,
        stock_query_service=ctx.stock_query_service,
        order_execution_service=orders,
        session_volume_service=common["session_volume_service"],
        market_clock=common["market_clock"],
        position_sizing_service=common["position_sizing_service"],
        market_regime_service=common["market_regime_service"],
        us_market_calendar_service=calendar,
        logger=ctx.logger,
        state_file=OverseasIntradayRSI2ShadowService.default_state_file(),
        top_n=getattr(cfg, "top_n", 20),
        max_positions=getattr(cfg, "max_positions", 5),
    )
