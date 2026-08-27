"""OverseasBootstrap — 해외(미국장) 서비스·태스크 조립을 전담한다.

`ServiceContainer.run()` 에서 옮겨온 4개 조립 경로를 담는다 — 관심종목 등락 알림,
dry-run 파이프라인(전략 6종 + suite + after-market 태스크), 수동 주문 게이팅 서비스,
장중 VBO 폴링 경로. 조립 순서·조건·후주입은 이관 전과 동일하다.

**자동 전략 경로의 `live_enabled=False` 잠금은 이 파일이 유일한 조립 지점이다** —
수동 주문 서비스(`live_enabled=True`)와 별도 인스턴스라는 분리가 여기서 유지된다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.market_clock import MarketClock
from repositories.favorite_repository import MARKET_OVERSEAS_US
from repositories.overseas_trade_repository import OverseasTradeRepository
from services.event_shadow_journal_service import EventShadowJournalService
from services.favorite_price_alert_service import FavoritePriceAlertService
from services.overseas_buyable_gap_up_dryrun_service import OverseasBuyableGapUpDryRunService
from services.overseas_candidate_service import OverseasCandidateService
from services.overseas_channel_breakout_dryrun_service import OverseasChannelBreakoutDryRunService
from services.overseas_dryrun_suite_service import OverseasDryRunSuiteService
from services.overseas_fill_reconcile_service import OverseasFillReconcileService
from services.overseas_intraday_buyable_gap_up_service import OverseasIntradayBuyableGapUpService
from services.overseas_intraday_channel_breakout_service import (
    OverseasIntradayChannelBreakoutService,
)
from services.overseas_intraday_pocket_pivot_service import OverseasIntradayPocketPivotService
from services.overseas_intraday_rsi2_service import OverseasIntradayRSI2Service
from services.overseas_intraday_squeeze_breakout_service import (
    OverseasIntradaySqueezeBreakoutService,
)
from services.overseas_intraday_vbo_service import OverseasIntradayVBOService
from services.overseas_order_execution_service import OverseasOrderExecutionService
from services.overseas_pocket_pivot_dryrun_service import OverseasPocketPivotDryRunService
from services.overseas_position_sizing_service import OverseasPositionSizingService, extract_fx_krw_per_usd
from services.overseas_rsi2_dryrun_service import OverseasRSI2DryRunService
from services.overseas_squeeze_breakout_dryrun_service import OverseasSqueezeBreakoutDryRunService
from services.overseas_vbo_dryrun_service import OverseasVBODryRunService
from services.us_market_calendar_service import USMarketCalendarService
from services.us_market_regime_service import USMarketRegimeService
from services.us_session_volume_service import USSessionVolumeService
from task.background.after_market.overseas_dryrun_task import OverseasDryRunTask
from task.background.intraday.overseas_favorite_price_alert_task import OverseasFavoritePriceAlertTask
from task.background.intraday.overseas_intraday_task import OverseasIntradayTask
from task.background.intraday.us_market_timing_daily_update_task import USMarketTimingDailyUpdateTask

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class OverseasBootstrap:
    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def build_favorite_price_alert(self, config_dict: dict, *, needs_web: bool) -> None:
        """미국장 관심종목 5% 단위 등락 알림 조립.

        해외는 실시간 스트림 경로가 없어 국내(웹소켓 틱)와 달리 REST 폴링 태스크가
        틱을 공급한다. 알림 상태는 ET 날짜 기준으로 리셋해야 하므로 국내 인스턴스와
        상태 파일·today_provider 를 분리한다.
        """
        ctx = self._ctx
        ctx.overseas_favorite_price_alert_service = None
        ctx.overseas_favorite_price_alert_task = None

        alert_cfg = config_dict.get("overseas_favorite_alert", {})
        if not isinstance(alert_cfg, dict):
            alert_cfg = {}
        if not needs_web or not alert_cfg.get("enabled", True):
            return
        if ctx.notification_service is None or getattr(ctx, "favorite_repo", None) is None:
            return

        us_clock = MarketClock.for_us_equities(logger=ctx.logger)
        ctx.overseas_favorite_price_alert_service = FavoritePriceAlertService(
            favorite_repository=ctx.favorite_repo,
            notification_service=ctx.notification_service,
            stock_code_repository=getattr(ctx, "overseas_stock_code_repository", None),
            market=MARKET_OVERSEAS_US,
            state_file="data/overseas_favorite_price_alert_state.json",
            today_provider=us_clock.get_current_kst_date_str,
            logger=ctx.logger,
        )
        ctx.overseas_favorite_price_alert_task = OverseasFavoritePriceAlertTask(
            favorite_repository=ctx.favorite_repo,
            broker=ctx.broker,
            alert_service=ctx.overseas_favorite_price_alert_service,
            market_clock=us_clock,
            overseas_stock_code_repository=getattr(ctx, "overseas_stock_code_repository", None),
            us_market_calendar_service=USMarketCalendarService(us_clock, logger=ctx.logger),
            check_interval_sec=alert_cfg.get("poll_interval_sec", 60),
            logger=ctx.logger,
        )

    def build_dryrun_pipeline(self) -> None:
        """해외 VBO/PP/BGU/CB/RSI2/OSB dry-run 파이프라인 조립 (주문 경로 없음 — 실주문 불가).

        overseas_us active 분기와 국내 active 공존 경로가 공유한다.
        `overseas_stock_code_repository` 가 없으면 dry-run 부분은 no-op(수동 주문 게이팅
        서비스는 종목코드 저장소와 무관하므로 그 전에 배선한다). shadow 저널은 realtime
        경로에서 만든 인스턴스를 재사용하고, 없으면(overseas active 등) 새로 만든다.
        """
        ctx = self._ctx
        ctx.overseas_candidate_service = None
        ctx.overseas_vbo_dryrun_service = None
        ctx.overseas_pp_dryrun_service = None
        ctx.overseas_bgu_dryrun_service = None
        ctx.overseas_cb_dryrun_service = None
        ctx.overseas_rsi2_dryrun_service = None
        ctx.overseas_osb_dryrun_service = None
        ctx.overseas_dryrun_task = None
        self._build_market_regime()
        if getattr(ctx, "event_shadow_journal_service", None) is None:
            ctx.event_shadow_journal_service = EventShadowJournalService(
                log_root="logs/strategies", logger=ctx.logger,
            )
        self._build_manual_order_service()
        if getattr(ctx, "overseas_stock_code_repository", None) is None:
            return
        overseas_stock_cfg = getattr(ctx.full_config, "overseas_stock", None)
        overseas_position_sizing_service = OverseasPositionSizingService(
            slot_usd=getattr(overseas_stock_cfg, "dryrun_slot_usd", 1000.0),
            max_qty=getattr(overseas_stock_cfg, "dryrun_max_qty", None),
            logger=ctx.logger,
        )
        ctx.overseas_candidate_service = OverseasCandidateService(
            overseas_stock_code_repository=ctx.overseas_stock_code_repository,
            stock_query_service=ctx.stock_query_service,
            logger=ctx.logger,
        )

        async def _overseas_fx_provider():
            # KIS 해외 잔고(읽기 전용)에서 USD/KRW 환율 추출. 실패 시 None → KRW 생략.
            try:
                resp = await ctx.broker.get_overseas_balance()
            except Exception:
                return None
            return extract_fx_krw_per_usd(getattr(resp, "data", None))

        ctx.overseas_vbo_dryrun_service = OverseasVBODryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        ctx.overseas_pp_dryrun_service = OverseasPocketPivotDryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        ctx.overseas_bgu_dryrun_service = OverseasBuyableGapUpDryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        ctx.overseas_cb_dryrun_service = OverseasChannelBreakoutDryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            indicator_service=ctx.indicator_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        ctx.overseas_rsi2_dryrun_service = OverseasRSI2DryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        ctx.overseas_osb_dryrun_service = OverseasSqueezeBreakoutDryRunService(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            shadow_journal=ctx.event_shadow_journal_service,
            logger=ctx.logger,
            position_sizing_service=overseas_position_sizing_service,
            fx_provider=_overseas_fx_provider,
        )
        overseas_dryrun_suite = OverseasDryRunSuiteService(
            [
                ctx.overseas_vbo_dryrun_service,
                ctx.overseas_pp_dryrun_service,
                ctx.overseas_bgu_dryrun_service,
                ctx.overseas_cb_dryrun_service,
                ctx.overseas_rsi2_dryrun_service,
                ctx.overseas_osb_dryrun_service,
            ],
            logger=ctx.logger,
        )
        # 미국 정규장 마감(16:00 ET) 직후 트리거. O-1: 규칙 기반 NYSE 캘린더를
        # 주입해 미국 휴장일에는 실행을 스킵한다 (기존: 주말 필터만).
        dryrun_us_clock = MarketClock.for_us_equities(logger=ctx.logger)
        ctx.overseas_dryrun_task = OverseasDryRunTask(
            dryrun_service=overseas_dryrun_suite,
            shadow_journal=ctx.event_shadow_journal_service,
            market_calendar_service=USMarketCalendarService(
                market_clock=dryrun_us_clock, logger=ctx.logger,
            ),
            market_clock=dryrun_us_clock,
            logger=ctx.logger,
            notification_service=ctx.notification_service,
            # Ticket-driven: 미국장 TimeDispatcher(time_dispatcher_us)가 NY 마감 후
            # 티켓을 발행하면 WorkerPool 이 execute() 를 호출한다(자체 AfterMarketLoop 미사용).
            worker_pool=ctx.worker_pool,
            # 국면은 기록 전용 — dry-run 은 관측 데이터라 차단하지 않는다.
            market_regime_service=ctx.us_market_regime_service,
        )
        self._build_intraday_strategies(overseas_stock_cfg, overseas_position_sizing_service)

    def _build_market_regime(self) -> None:
        """미국장 국면 판정 서비스 + 개장 전 일일 갱신 태스크 조립.

        KIS 에 해외 지수 TR 이 없어 프록시 ETF(QQQ/NASD) 일봉으로 국내와 동일한 MA
        추세 로직을 돌린다. 소비처는 장중 VBO 신규 진입 게이트이며, 마감 후 dry-run
        은 라벨만 기록한다(차단 없음 — 게이트의 사후 검증용 관측 데이터 보존).
        """
        ctx = self._ctx
        us_clock = MarketClock.for_us_equities(logger=ctx.logger)
        us_calendar = USMarketCalendarService(us_clock, logger=ctx.logger)
        ctx.us_market_regime_service = USMarketRegimeService(
            stock_query_service=ctx.stock_query_service,
            market_clock=us_clock,
            logger=ctx.logger,
            us_market_calendar_service=us_calendar,
            notification_service=ctx.notification_service,
        )
        # 알림이 없으면 태스크가 할 일이 없다 — 국면 캐시는 거래일이 바뀌면 자동 갱신된다.
        ctx.us_market_timing_daily_update_task = USMarketTimingDailyUpdateTask(
            us_market_regime_service=ctx.us_market_regime_service,
            market_clock=us_clock,
            us_market_calendar_service=us_calendar,
            logger=ctx.logger,
        ) if ctx.notification_service is not None else None

    def _build_manual_order_service(self) -> None:
        """웹 수동 해외주문(`POST /api/overseas/order`) 전용 게이팅 서비스 배선.

        라우트가 broker 를 직접 호출하면 kill-switch 와 저널 기록을 우회한다(국내 수동
        주문은 `OrderExecutionService` 경유라 둘 다 걸린다). 수동 주문은 원래 발사되는
        경로이므로 `live_enabled=True` 이며, **자동 전략 경로는 별도 인스턴스**라
        `live_enabled=False` 잠금이 그대로 유지된다.
        """
        ctx = self._ctx
        # 미국 거래는 원화 원장(VirtualTradeRepository)이 아니라 USD 전용 원장에 남긴다.
        ctx.overseas_trade_repository = OverseasTradeRepository()
        # 원장은 주문 접수 시점 기록이라 미체결 지정가도 HOLD 로 잡힌다 — 브로커 체결내역
        # 대조로 사후 보정한다(`POST /api/overseas/trades/reconcile`).
        ctx.overseas_fill_reconcile_service = OverseasFillReconcileService(
            trade_repository=ctx.overseas_trade_repository,
            stock_query_service=ctx.stock_query_service,
            logger=ctx.logger,
        )
        ctx.overseas_manual_order_service = OverseasOrderExecutionService(
            broker=ctx.broker,
            live_enabled=True,
            journal=ctx.event_shadow_journal_service,
            kill_switch=getattr(ctx, "kill_switch_service", None),
            notification_service=ctx.notification_service,
            journal_strategy_name="수동매매_해외",
            logger=ctx.logger,
        )

    def _build_intraday_strategies(self, overseas_stock_cfg, position_sizing_service) -> None:
        """해외 장중 전략 폴링 경로 조립 (전략별 config 로 opt-in, 기본 전부 off).

        dry-run 은 마감 후 사후 평가라 발사 대상이 없다. 본 경로는 정규장 중 폴링가로
        진입/청산을 판정해 전략이 실제로 돌게 한다. 다만 **주문 서비스는
        `live_enabled=False` 로 고정**한다 — 해외 주문 TR 은 실전만 존재하고 Phase 5
        (canary/kill-switch/reconcile) 가 미완이라, 자동 발사는 여전히 잠근다.
        `allow_live_trading` 은 수동 주문 경로용이며 이 자동 경로를 열지 않는다.

        전략 6종은 **하나의 태스크·하나의 폴링 패스**를 공유한다 — 전략마다 태스크를
        두면 겹치는 심볼을 전략 수만큼 중복 조회한다. 주문 서비스와 paper 저널도
        공유하므로 전략을 켜도 늘어나는 것은 판정 비용뿐이다.

        신규 진입은 `USMarketRegimeService` 국면 게이트를 통과해야 한다
        (전략별 `market_timing_gate: false` 로 해제 가능). 손절/EOD 청산은 게이트 대상이 아니다.
        """
        ctx = self._ctx
        ctx.overseas_intraday_vbo_service = None
        ctx.overseas_intraday_cb_service = None
        ctx.overseas_intraday_rsi2_service = None
        ctx.overseas_intraday_bgu_service = None
        ctx.overseas_intraday_osb_service = None
        ctx.overseas_intraday_pp_service = None
        ctx.overseas_intraday_task = None

        vbo_cfg = getattr(overseas_stock_cfg, "intraday_vbo", None)
        enabled_any = getattr(vbo_cfg, "enabled", False) or any(
            getattr(getattr(overseas_stock_cfg, attr, None), "enabled", False)
            for attr in (
                "intraday_channel_breakout", "intraday_rsi2", "intraday_buyable_gap_up",
                "intraday_squeeze_breakout", "intraday_pocket_pivot",
            )
        )
        if not enabled_any:
            return

        # 이 경로 전용 저널 — 국내 event_shadow 와 버퍼를 공유하면 틱마다 flush 할 때
        # 남의 기록이 US 거래일 파일로 딸려간다. 파일은 같은 디렉토리에 append 되므로
        # 소비 측(analyze/compare)은 signal_source 로 구분한다.
        paper_journal = EventShadowJournalService(log_root="logs/strategies", logger=ctx.logger)
        order_execution_service = OverseasOrderExecutionService(
            broker=None,  # live_enabled=False 이므로 broker 미사용(구조적 잠금)
            live_enabled=False,
            journal=paper_journal,
            notification_service=ctx.notification_service,
            logger=ctx.logger,
        )
        intraday_us_clock = MarketClock.for_us_equities(logger=ctx.logger)
        us_calendar = USMarketCalendarService(
            market_clock=intraday_us_clock, logger=ctx.logger,
        )
        session_volume_service = USSessionVolumeService(
            us_market_calendar_service=us_calendar, logger=ctx.logger,
        )

        common = dict(
            candidate_service=ctx.overseas_candidate_service,
            stock_query_service=ctx.stock_query_service,
            order_execution_service=order_execution_service,
            session_volume_service=session_volume_service,
            market_clock=intraday_us_clock,
            position_sizing_service=position_sizing_service,
            market_regime_service=ctx.us_market_regime_service,
            logger=ctx.logger,
        )

        def _opts(cfg):
            return dict(
                top_n=getattr(cfg, "top_n", 20),
                max_positions=getattr(cfg, "max_positions", 5),
                market_timing_gate=getattr(cfg, "market_timing_gate", True),
            )

        services = []
        if getattr(vbo_cfg, "enabled", False):
            # VBO 는 공통 베이스 이전에 만들어진 독립 구현이라 생성자 인자가 다르다.
            ctx.overseas_intraday_vbo_service = OverseasIntradayVBOService(
                candidate_service=ctx.overseas_candidate_service,
                stock_query_service=ctx.stock_query_service,
                order_execution_service=order_execution_service,
                position_sizing_service=position_sizing_service,
                logger=ctx.logger,
                k_value=getattr(vbo_cfg, "k_value", 0.5),
                stop_loss_pct=getattr(vbo_cfg, "stop_loss_pct", -3.0),
                market_regime_service=ctx.us_market_regime_service,
                **_opts(vbo_cfg),
            )
            services.append(ctx.overseas_intraday_vbo_service)

        cb_cfg = getattr(overseas_stock_cfg, "intraday_channel_breakout", None)
        if getattr(cb_cfg, "enabled", False):
            ctx.overseas_intraday_cb_service = OverseasIntradayChannelBreakoutService(
                indicator_service=ctx.indicator_service, **common, **_opts(cb_cfg),
            )
            services.append(ctx.overseas_intraday_cb_service)

        rsi2_cfg = getattr(overseas_stock_cfg, "intraday_rsi2", None)
        if getattr(rsi2_cfg, "enabled", False):
            ctx.overseas_intraday_rsi2_service = OverseasIntradayRSI2Service(
                us_market_calendar_service=us_calendar, **common, **_opts(rsi2_cfg),
            )
            services.append(ctx.overseas_intraday_rsi2_service)

        bgu_cfg = getattr(overseas_stock_cfg, "intraday_buyable_gap_up", None)
        if getattr(bgu_cfg, "enabled", False):
            ctx.overseas_intraday_bgu_service = OverseasIntradayBuyableGapUpService(
                **common, **_opts(bgu_cfg),
            )
            services.append(ctx.overseas_intraday_bgu_service)

        osb_cfg = getattr(overseas_stock_cfg, "intraday_squeeze_breakout", None)
        if getattr(osb_cfg, "enabled", False):
            ctx.overseas_intraday_osb_service = OverseasIntradaySqueezeBreakoutService(
                **common, **_opts(osb_cfg),
            )
            services.append(ctx.overseas_intraday_osb_service)

        pp_cfg = getattr(overseas_stock_cfg, "intraday_pocket_pivot", None)
        if getattr(pp_cfg, "enabled", False):
            ctx.overseas_intraday_pp_service = OverseasIntradayPocketPivotService(
                **common, **_opts(pp_cfg),
            )
            services.append(ctx.overseas_intraday_pp_service)

        ctx.overseas_intraday_task = OverseasIntradayTask(
            strategy_services=services,
            broker=ctx.broker,
            market_clock=intraday_us_clock,
            us_market_calendar_service=us_calendar,
            shadow_journal=paper_journal,
            check_interval_sec=getattr(vbo_cfg, "poll_interval_sec", 60),
            session_prepare_delay_min=getattr(vbo_cfg, "session_prepare_delay_min", 5),
            eod_exit_before_min=getattr(vbo_cfg, "eod_exit_before_min", 10),
            logger=ctx.logger,
        )
