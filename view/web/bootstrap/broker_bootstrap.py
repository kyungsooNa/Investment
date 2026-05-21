"""BrokerBootstrap — `WebAppContext._bootstrap_broker()` 본문을 전담한다.

토큰 발급, BrokerAPIWrapper 생성, MarketCalendarService 동기화까지의
범위만 책임진다. 후주입(`_mcs.set_broker(...)`)은 본 PR 범위 외로
그대로 유지한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from brokers.broker_api_wrapper import BrokerAPIWrapper

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class BrokerBootstrap:
    """`WebAppContext` 의 브로커 초기화 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    async def run(self, is_paper_trading: bool) -> bool:
        ctx = self._ctx
        try:
            token_acquired = await ctx.env.get_access_token()
            if not token_acquired:
                ctx.logger.critical("[BrokerBootstrap] 토큰 발급 실패.")
                return False
            # 모의투자 모드에서도 실전 토큰 사전 발급 (조회 API는 항상 실전 인증 사용)
            if is_paper_trading:
                await ctx.env.get_real_access_token()
            ctx.broker = BrokerAPIWrapper(
                env=ctx.env,
                logger=ctx.logger,
                market_clock=ctx.market_clock,
                market_calendar_service=ctx._mcs,
                streaming_logger=ctx.streaming_event_logger,
                stock_code_repository=ctx.stock_code_repository,
                api_budget_limiter=getattr(ctx, "api_budget_limiter", None),
            )
            ctx._mcs.set_broker(ctx.broker)
            await ctx._mcs._sync_calendar_if_needed()
        except Exception as e:
            ctx.logger.critical(f"[BrokerBootstrap] 초기화 실패: {e}", exc_info=True)
            return False
        return True
