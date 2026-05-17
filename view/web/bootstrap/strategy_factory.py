"""StrategyFactory — `WebAppContext.initialize_scheduler()` 본문을 전담한다.

StrategyScheduler 생성과 7개 활성 전략 등록, BackgroundScheduler 에
StrategySchedulerTaskAdapter 등록까지 담당한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_strategy_logger
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.larry_williams_channel_breakout_strategy import LarryWilliamsChannelBreakoutStrategy
from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
from task.background.intraday.strategy_scheduler_task_adapter import StrategySchedulerTaskAdapter
from view.web.bootstrap.runtime_mode import RuntimeMode

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class StrategyFactory:
    """`WebAppContext` 의 전략 스케줄러 + 전략 등록을 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def build(self) -> None:
        ctx = self._ctx
        if not (ctx.runtime_mode & RuntimeMode.TRADING):
            ctx.logger.info(
                "[StrategyFactory] runtime_mode=%s — TRADING 비활성, StrategyScheduler 미생성.",
                ctx.runtime_mode,
            )
            return
        ctx.scheduler = StrategyScheduler(
            virtual_trade_service=ctx.virtual_trade_service,
            order_execution_service=ctx.order_execution_service,
            stock_query_service=ctx.stock_query_service,
            stock_code_repository=ctx.stock_code_repository,
            market_clock=ctx.market_clock,
            market_calendar_service=ctx._mcs,
            logger=get_strategy_logger('StrategyScheduler'),
            dry_run=False,
            notification_service=ctx.notification_service,
            performance_profiler=ctx.pm,
            price_subscription_service=ctx.price_subscription_service,
            kill_switch_service=ctx.kill_switch_service,
            account_snapshot_cache=ctx.account_snapshot_cache,
            position_sizing_service=ctx.position_sizing_service,
        )

        # 오닐 스퀴즈 돌파 전략 등록
        osb_strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('OneilSqueezeBreakout'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=osb_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 👈 오닐 전략은 오버나잇(홀딩) 허용!
            allow_pyramiding=True,     # 👈 오버나잇 전략이므로 불타기 허용
            scan_when_position_full=True,
        ))

        ctx.osb_strategy = osb_strategy  # (웹 API 하위 호환성 유지용)
        ctx.oneil_universe_service_ref = ctx.oneil_universe_service

        # 오닐 포켓 피봇 & BGU 전략 등록
        pp_strategy = OneilPocketPivotStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('OneilPocketPivot'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=pp_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 7주 홀딩 허용
            allow_pyramiding=True,      # 👈 오버나잇 전략이므로 불타기 허용
            scan_when_position_full=True,
        ))

        # 하이 타이트 플래그 전략 등록
        htf_strategy = HighTightFlagStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('HighTightFlag'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=htf_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # HTF는 오버나잇 홀딩 허용
            scan_when_position_full=True,
        ))

        # 첫 눌림목(Holy Grail) 전략 등록
        fp_strategy = FirstPullbackStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('FirstPullback'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=fp_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 스윙 전략: 오버나잇 허용
            allow_pyramiding=False,
            scan_when_position_full=True,
        ))
        # 래리 윌리엄스 변동성 돌파 전략 등록
        vbo_strategy = LarryWilliamsVBOStrategy(
            stock_query_service=ctx.stock_query_service,
            market_clock=ctx.market_clock,
            universe_service=ctx.oneil_universe_service,
            logger=get_strategy_logger('LarryWilliamsVBO'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=vbo_strategy,
            interval_minutes=5,
            max_positions=3,
            order_qty=1,
            enabled=False,
            force_exit_on_close=True,   # 오버나이트 금지 — 당일 장 마감 전 강제 청산
            allow_pyramiding=False,
            scan_when_position_full=True,
        ))
        # 래리 코너스 RSI(2) 눌림목 전략 등록
        rsi2_strategy = RSI2PullbackStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            indicator_service=ctx.indicator_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('RSI2Pullback'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=rsi2_strategy,
            interval_minutes=5,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 평균 보유 ~2.5일 — 오버나잇 허용
            allow_pyramiding=False,     # 1종목 1회 진입 invariant
            scan_when_position_full=True,
        ))
        # 래리 윌리엄스 / 펜볼드 돈천 채널 돌파 전략 등록
        lwcb_strategy = LarryWilliamsChannelBreakoutStrategy(
            stock_query_service=ctx.stock_query_service,
            universe_service=ctx.oneil_universe_service,
            indicator_service=ctx.indicator_service,
            market_clock=ctx.market_clock,
            logger=get_strategy_logger('LarryWilliamsCB'),
        )
        ctx.scheduler.register(StrategySchedulerConfig(
            strategy=lwcb_strategy,
            interval_minutes=5,
            max_positions=5,
            order_qty=1,
            enabled=False,              # 수동 활성화 대기
            force_exit_on_close=False,  # 스윙 포지션 — 오버나잇 허용
            allow_pyramiding=False,     # 1종목 1포지션 invariant
            scan_when_position_full=True,
        ))

        ctx.logger.info("웹 앱: 전략 스케줄러 초기화 완료 (수동 시작 대기)")

        # StrategyScheduler를 BackgroundScheduler에 어댑터로 등록
        if ctx.background_scheduler and ctx.scheduler:
            adapter = StrategySchedulerTaskAdapter(ctx.scheduler, market_clock=ctx.market_clock)
            ctx.background_scheduler.register(adapter)
