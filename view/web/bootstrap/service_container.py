"""ServiceContainer — `WebAppContext._bootstrap_services()` 본문을 전담한다.

저장소, 도메인 서비스, 백그라운드 태스크, 주문 파이프라인, 유니버스
서비스까지 8개의 try-except 블록을 그대로 옮긴다. 후주입 패턴
(`_minervini_update_task`, `data_quality_service.set_price_stream_service`,
`streaming_service.set_streaming_stock_repo` 등)도 그대로 유지한다.
세부 분해와 후주입 정리는 후속 PR 에서 진행한다.
"""
from __future__ import annotations

import os
from typing import Any, Optional, TYPE_CHECKING

from common.types import ErrorCode, Exchange
from config.config_loader import (
    AiAnalysisConfig,
    DartDisclosureConfig,
    OrderPolicyConfig,
    PaperAccountExpiryAlertConfig,
    PositionSizingConfig,
    RiskGateConfig,
    TradeTrendMonitorConfig,
    YoutubeDigestConfig,
)
from core.account_snapshot import AccountSnapshotCache
from scheduler.strategy_scheduler_store import StrategySchedulerStore
from services.backtest_microstructure_capture import BacktestMicrostructureCaptureService
from services.execution_flow_service import ExecutionFlowService
from services.deferred_order_queue import DeferredOrderQueue
from services.ai_client import AiClient
from services.ai_usage_limiter import AiUsageLimiter
from services.ai_analysis_service import AIAnalysisService
from services.ai_disclosure_analyzer import AiDisclosureAnalyzer
from services.ai_stock_analyzer import AiStockAnalyzer
from services.premium_watchlist_ai_analysis_service import PremiumWatchlistAiAnalysisService
from services.ai_news_analyzer import AiNewsAnalyzer
from services.stock_news_collector_service import StockNewsCollectorService
from services.dart_disclosure_client import DartDisclosureClient
from services.dart_disclosure_rule_service import DartDisclosureRuleService
from services.trade_trend_service import CustomsTradeStatClient, NationalTradeTrendWebClient
from services.minervini_stage_service import MinerviniStageService
from services.naver_finance_scraper_service import NaverFinanceScraperService
from services.newhigh_service import NewHighService
from services.oneil_universe_service import OneilUniverseService
from services.theme_classification_collector_service import ThemeClassificationCollectorService
from services.us_market_calendar_service import USMarketCalendarService
from task.background.after_market.theme_classification_task import ThemeClassificationTask
from services.opening_position_reconcile_service import OpeningPositionReconcileService
from services.order_execution_service import OrderExecutionService
from services.order_policy_service import OrderPolicyService
from services.paper_account_expiry_alert_service import PaperAccountExpiryAlertService
from services.position_sizing_service import PositionSizingService
from services.risk_gate_service import RiskGateService
from services.strategy_log_report_service import StrategyLogReportService
from task.background.after_market.after_market_reconcile_task import AfterMarketReconcileTask
from task.background.after_market.cache_warmup_task import CacheWarmupTask
from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
from task.background.after_market.log_cleanup_task import LogCleanupTask
from task.background.after_market.microstructure_capture_task import MicrostructureCaptureTask
from task.background.after_market.minervini_update_task import MinerviniUpdateTask
from task.background.after_market.newhigh_task import NewHighTask
from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from task.background.after_market.premium_watchlist_generator_task import PremiumWatchlistGeneratorTask
from task.background.after_market.strategy_log_report_task import StrategyLogReportTask
from task.background.after_market.theme_daily_leader_report_task import ThemeDailyLeaderReportTask
from task.background.always_on.notification_queue_task import NotificationQueueTask
from task.background.always_on.dart_disclosure_monitor_task import DartDisclosureMonitorTask
from task.background.always_on.trade_trend_monitor_task import TradeTrendMonitorTask
from task.background.intraday.opening_position_reconcile_task import OpeningPositionReconcileTask
from task.background.intraday.pre_market_health_check_task import PreMarketHealthCheckTask
from task.background.intraday.paper_account_expiry_alert_task import PaperAccountExpiryAlertTask
from task.background.intraday.program_capture_subscription_task import ProgramCaptureSubscriptionTask
from task.background.intraday.theme_intraday_leader_alert_task import ThemeIntradayLeaderAlertTask
from task.background.intraday.market_index_threshold_alert_task import MarketIndexThresholdAlertTask
from task.background.intraday.market_timing_daily_update_task import MarketTimingDailyUpdateTask
from view.web.bootstrap.runtime_mode import RuntimeMode
from view.web.bootstrap.backtest_task_bootstrap import BacktestTaskBootstrap
from view.web.bootstrap.repository_bootstrap import RepositoryBootstrap
from view.web.bootstrap.market_data_bootstrap import MarketDataBootstrap
from view.web.bootstrap.query_bootstrap import QueryBootstrap
from view.web.bootstrap.realtime_bootstrap import RealtimeBootstrap
from view.web.bootstrap.overseas_bootstrap import OverseasBootstrap
from view.web.market_mode_utils import is_market_enabled
from repositories.dart_disclosure_repository import DartDisclosureRepository
from repositories.youtube_channel_repository import YoutubeChannelRepository
from repositories.youtube_digest_repository import YoutubeDigestRepository
from services.gemini_youtube_video_analyzer_service import (
    GeminiYoutubeVideoAnalyzerService,
)
from services.youtube_transcript_collector_service import YoutubeTranscriptCollectorService
from services.youtube_digest_service import YoutubeDigestService
from task.background.intraday.youtube_digest_task import YoutubeDigestTask

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


def _extract_int_field(data: Any, *keys: str) -> Optional[int]:
    """API 응답 dict/list/dataclass에서 첫 양수 정수 필드를 추출. 실패 시 None."""
    if data is None:
        return None
    candidates: list = []
    if isinstance(data, dict):
        candidates.append(data)
        for sub_key in ("output", "output1"):
            sub = data.get(sub_key)
            if isinstance(sub, dict):
                candidates.append(sub)
    elif isinstance(data, list):
        candidates.extend(item for item in data if isinstance(item, dict))
    else:
        candidates.append(data)
    for item in candidates:
        for key in keys:
            try:
                if isinstance(item, dict):
                    val = item.get(key)
                else:
                    val = getattr(item, key, None)
            except Exception:
                continue
            if val is None or val == "":
                continue
            try:
                v = int(str(val).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
    return None


def _config_value(section: Any, key: str, default: Any) -> Any:
    if isinstance(section, dict):
        return section.get(key, default)
    return getattr(section, key, default)


async def _resolve_market_buy_reference_price(
    broker: Any,
    logger: Any,
    stock_code: str,
    exchange: Exchange,
) -> Optional[int]:
    """시장가 매수 RiskGate 검증용 기준가격.

    최우선매도호가(시장가 매수 체결 추정가) → 현재가 순으로 fallback. 둘 다 실패 시 None.
    """
    if broker is None:
        return None
    success = ErrorCode.SUCCESS.value
    try:
        resp = await broker.get_asking_price(stock_code, exchange=exchange)
        if resp is not None and getattr(resp, "rt_cd", None) == success:
            ask = _extract_int_field(resp.data, "askp1", "매도호가1")
            if ask and ask > 0:
                return ask
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "[RiskGate] asking_price provider failed for %s: %s", stock_code, exc
            )
    try:
        resp = await broker.get_current_price(stock_code, exchange=exchange)
        if resp is not None and getattr(resp, "rt_cd", None) == success:
            cur = _extract_int_field(resp.data, "stck_prpr")
            if cur and cur > 0:
                return cur
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "[RiskGate] current_price provider failed for %s: %s", stock_code, exc
            )
    return None


class ServiceContainer:
    """`WebAppContext` 의 서비스 조립 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx
        mode = getattr(ctx, "runtime_mode", RuntimeMode.ALL)
        needs_web = bool(mode & RuntimeMode.WEB)
        needs_trading = bool(mode & RuntimeMode.TRADING)
        needs_batch = bool(mode & RuntimeMode.BATCH)
        needs_realtime = bool(mode & (RuntimeMode.WEB | RuntimeMode.TRADING))
        is_overseas_us = getattr(ctx, "market_mode", "domestic") == "overseas_us"
        if is_overseas_us:
            # 해외주식 v1은 조회 + 수동 지정가 주문만 지원한다.
            needs_trading = False
            needs_batch = False
            needs_realtime = False

        config_dict = ctx.full_config
        if hasattr(config_dict, "model_dump"):
            config_dict = config_dict.model_dump()
        elif hasattr(config_dict, "dict"):
            config_dict = config_dict.dict()

        cache_store = RepositoryBootstrap(ctx).run(config_dict)
        MarketDataBootstrap(
            ctx,
            us_market_calendar_factory=USMarketCalendarService,
        ).run(cache_store)

        QueryBootstrap(
            ctx,
            us_market_calendar_factory=USMarketCalendarService,
        ).run(
            config=config_dict,
            is_overseas_us=is_overseas_us,
            needs_batch=needs_batch,
        )

        # AI 분석 클라이언트 (Gemini/Groq/Ollama OpenAI 호환) — 1차: 공시 요약,
        # 2차(종목 분석)에서 ctx.ai_client 재사용. provider 차이는 config 로 흡수.
        ctx.ai_client = None
        ctx.ai_usage_limiter = None
        ctx.ai_analysis_service = None
        ctx.ai_disclosure_analyzer = None
        ctx.ai_stock_analyzer = None
        ctx.ai_news_analyzer = None
        # 뉴스 수집 자체는 AI 와 무관하므로 AI 비활성 상태에서도 항상 생성한다.
        ctx.stock_news_collector = StockNewsCollectorService(logger=ctx.logger)
        raw_ai_config = config_dict.get("ai_analysis") or {}
        ai_config = AiAnalysisConfig.model_validate(raw_ai_config)
        if ai_config.enabled and ai_config.base_url and ai_config.model:
            ctx.ai_usage_limiter = AiUsageLimiter(
                daily_request_limit=int(ai_config.daily_request_limit),
                disclosure_reserve=int(ai_config.disclosure_reserve),
            )
            ctx.ai_client = AiClient(
                base_url=ai_config.base_url,
                api_key=ai_config.api_key,
                model=ai_config.model,
                timeout_sec=float(ai_config.timeout_sec),
                reasoning_effort=ai_config.reasoning_effort,
                usage_limiter=ctx.ai_usage_limiter,
                max_concurrent_requests=int(ai_config.max_concurrent_requests),
                min_request_interval_sec=float(ai_config.min_request_interval_sec),
                max_input_chars=int(ai_config.max_input_chars),
                logger=ctx.logger,
            )
            ctx.ai_stock_analyzer = AiStockAnalyzer(
                ctx.ai_client, max_tokens=int(ai_config.max_tokens)
            )
            ctx.ai_news_analyzer = AiNewsAnalyzer(
                ctx.ai_client, max_tokens=int(ai_config.max_tokens)
            )
            ctx.ai_analysis_service = AIAnalysisService(
                ctx.ai_client,
                provider_name=ai_config.provider,
                model=ai_config.model,
                logger=ctx.logger,
                max_tokens=int(ai_config.max_tokens),
            )
            if ai_config.disclosure_summary_enabled:
                ctx.ai_disclosure_analyzer = AiDisclosureAnalyzer(
                    ctx.ai_client, logger=ctx.logger, max_tokens=int(ai_config.max_tokens)
                )

        ctx.dart_disclosure_client = None
        ctx.dart_disclosure_repository = None
        ctx.dart_disclosure_rule_service = None
        ctx.dart_disclosure_monitor_task = None
        raw_dart_config = config_dict.get("dart_disclosure") or {}
        dart_config = DartDisclosureConfig.model_validate(raw_dart_config)
        if dart_config.enabled:
            if not needs_web or is_overseas_us:
                ctx.logger.info("OpenDART 공시 모니터는 국내 WEB 런타임에서만 동작합니다.")
            elif not dart_config.api_key:
                ctx.logger.warning("OpenDART 공시 모니터가 활성화됐지만 API 키가 없어 비활성화합니다.")
            elif getattr(ctx, "telegram_reporter", None) is None:
                ctx.logger.warning("OpenDART 공시 모니터가 활성화됐지만 텔레그램 리포터가 없어 비활성화합니다.")
            else:
                ctx.dart_disclosure_client = DartDisclosureClient(
                    dart_config.api_key,
                    timeout_sec=float(dart_config.request_timeout_sec),
                )
                ctx.dart_disclosure_repository = DartDisclosureRepository()
                ctx.dart_disclosure_rule_service = DartDisclosureRuleService()
                ctx.dart_disclosure_monitor_task = DartDisclosureMonitorTask(
                    client=ctx.dart_disclosure_client,
                    repository=ctx.dart_disclosure_repository,
                    favorite_repository=ctx.favorite_repo,
                    rule_service=ctx.dart_disclosure_rule_service,
                    telegram_reporter=ctx.telegram_reporter,
                    config=dart_config,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    ai_analyzer=ctx.ai_disclosure_analyzer,
                    notification_service=ctx.notification_service,
                )

        ctx.trade_trend_monitor_task = None
        raw_trade_trend_config = config_dict.get("trade_trend_monitor") or {}
        trade_trend_config = TradeTrendMonitorConfig.model_validate(raw_trade_trend_config)
        # 제주지역 수출입동향 웹 조회는 모니터 활성화 여부와 무관하게 관세청 키만 있으면 쓴다.
        ctx.trade_trend_customs_client = None
        if needs_web and trade_trend_config.customs_service_key:
            ctx.trade_trend_customs_client = CustomsTradeStatClient(
                service_key=trade_trend_config.customs_service_key,
                base_url=trade_trend_config.customs_base_url,
                item_base_url=trade_trend_config.customs_item_base_url,
                timeout_sec=float(trade_trend_config.request_timeout_sec),
                sido_param_name=trade_trend_config.sido_param_name,
                sido_code=trade_trend_config.sido_code,
            )
        if trade_trend_config.enabled:
            if not needs_web:
                ctx.logger.info("수출입동향 모니터는 WEB 런타임에서만 동작합니다.")
            elif getattr(ctx, "telegram_reporter", None) is None:
                ctx.logger.warning("수출입동향 모니터가 활성화됐지만 텔레그램 리포터가 없어 비활성화합니다.")
            else:
                monitor_customs_client = None
                if trade_trend_config.jeju_semiconductor_enabled:
                    if not trade_trend_config.customs_service_key:
                        ctx.logger.warning(
                            "제주 반도체 수출 모니터는 관세청 API 키가 없어 스킵합니다."
                        )
                    else:
                        monitor_customs_client = ctx.trade_trend_customs_client
                ctx.national_trade_trend_client = NationalTradeTrendWebClient(
                    list_urls=list(trade_trend_config.national_list_urls),
                    timeout_sec=float(trade_trend_config.request_timeout_sec),
                    max_detail_pages=int(trade_trend_config.national_max_detail_pages),
                )
                ctx.trade_trend_monitor_task = TradeTrendMonitorTask(
                    customs_client=monitor_customs_client,
                    national_client=ctx.national_trade_trend_client,
                    repository_path=trade_trend_config.state_file_path,
                    telegram_reporter=ctx.telegram_reporter,
                    config=trade_trend_config,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                )

        try:
            if is_overseas_us:
                ctx.minervini_stage_service = None
            else:
                ctx.minervini_stage_service = MinerviniStageService(
                    stock_query_service=ctx.stock_query_service,
                    rs_rating_service=getattr(ctx, "rs_rating_service", None),
                    stock_repository=ctx.stock_repository,
                    logger=ctx.logger,
                )
                # 차트 일자별 Stage 표기를 위해 StockQueryService에 후주입.
                ctx.stock_query_service.set_minervini_stage_service(ctx.minervini_stage_service)
            # NOTE: favorite_service.minervini_stage_service wiring is performed by WiringPhase.
        except Exception as e:
            ctx.logger.warning(f"[ServiceBootstrap:MinerviniStage] 초기화 실패: {e}")
            ctx.minervini_stage_service = None

        try:
            ctx.minervini_update_task = None
            if needs_batch:
                ctx.minervini_update_task = MinerviniUpdateTask(
                    minervini_service=getattr(ctx, 'minervini_stage_service', None),
                    stock_code_repository=ctx.stock_code_repository,
                    stock_repository=ctx.stock_repository,
                    stock_query_service=ctx.stock_query_service,
                    broker_api_wrapper=ctx.broker,
                    rs_rating_service=getattr(ctx, 'rs_rating_service', None),
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    performance_profiler=ctx.pm,
                    notification_service=ctx.notification_service,
                    telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                    market_calendar_service=ctx._mcs,
                    worker_pool=ctx.worker_pool,
                )
        except Exception as e:
            ctx.logger.warning(f"[ServiceBootstrap:MinerviniUpdate] 초기화 실패: {e}")
            ctx.minervini_update_task = None

        # NOTE: minervini_stage_service ↔ minervini_update_task circular wiring is performed by WiringPhase.

        try:
            RealtimeBootstrap(ctx).run(
                config=config_dict,
                needs_realtime=needs_realtime,
            )

            if needs_trading:
                ctx.pre_market_health_check_task = PreMarketHealthCheckTask(
                    broker=ctx.broker,
                    env=ctx.env,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    streaming_stock_repo=ctx.streaming_stock_repo,
                    data_quality_service=ctx.data_quality_service,
                    notification_service=ctx.notification_service,
                    logger=ctx.logger,
                )
                expiry_cfg = PaperAccountExpiryAlertConfig.model_validate(
                    config_dict.get("paper_account_expiry_alert") or {}
                )
                ctx.paper_account_expiry_alert_task = (
                    PaperAccountExpiryAlertTask(
                        alert_service=PaperAccountExpiryAlertService(
                            expires_at=expiry_cfg.expires_at,
                            warning_days=expiry_cfg.warning_days,
                            notification_service=ctx.notification_service,
                            market_clock=ctx.market_clock,
                            state_file=expiry_cfg.state_file_path,
                            logger=ctx.logger,
                        ),
                        env=ctx.env,
                        market_clock=ctx.market_clock,
                        logger=ctx.logger,
                        check_interval_sec=expiry_cfg.check_interval_sec,
                    )
                    if expiry_cfg.enabled
                    else None
                )
            else:
                ctx.pre_market_health_check_task = None
                ctx.paper_account_expiry_alert_task = None

            if needs_batch:
                ctx.daily_price_collector_task = DailyPriceCollectorTask(
                    stock_query_service=ctx.stock_query_service,
                    stock_code_repository=ctx.stock_code_repository,
                    stock_repo=ctx.stock_repository,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    performance_profiler=ctx.pm,
                    notification_service=ctx.notification_service,
                    logger=ctx.logger,
                    rs_rating_service=getattr(ctx, "rs_rating_service", None),
                    worker_pool=ctx.worker_pool,
                )
            else:
                ctx.daily_price_collector_task = None
            # NOTE: minervini_update_task._daily_price_collector_task wiring is performed by WiringPhase.
            if needs_batch:
                ctx.ohlcv_update_task = OhlcvUpdateTask(
                    stock_query_service=ctx.stock_query_service,
                    stock_code_repository=ctx.stock_code_repository,
                    stock_repo=ctx.stock_repository,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    performance_profiler=ctx.pm,
                    notification_service=ctx.notification_service,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                )
            else:
                ctx.ohlcv_update_task = None
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:Streaming] 초기화 실패: {e}", exc_info=True)
            raise

        # 유튜브 일일 다이제스트. 채널 저장소는 UI 관리용이라 항상 만들고, 요약 태스크는
        # 본체가 AI 라서 AI 비활성 시 만들지 않는다.
        ctx.youtube_channel_repository = YoutubeChannelRepository()
        ctx.youtube_digest_repository = YoutubeDigestRepository()
        raw_youtube_config = config_dict.get("youtube_digest") or {}
        youtube_config = YoutubeDigestConfig.model_validate(raw_youtube_config)
        ctx.youtube_transcript_collector = YoutubeTranscriptCollectorService(
            logger=ctx.logger,
            proxy_type=youtube_config.proxy_type,
            proxy_username=youtube_config.proxy_username,
            proxy_password=youtube_config.proxy_password,
            proxy_http_url=youtube_config.proxy_http_url,
            proxy_https_url=youtube_config.proxy_https_url,
            proxy_locations=tuple(youtube_config.proxy_locations),
            request_interval_sec=float(youtube_config.request_interval_sec),
        )
        ctx.youtube_digest_service = None
        ctx.youtube_digest_task = None
        ctx.youtube_gemini_fallback_service = None
        if ctx.ai_client is not None:
            # 청크는 입력 예산보다 작아야 AiClient 가 뒤를 잘라내지 않는다.
            _yt_budget = int(ai_config.max_input_chars) or 24000
            ctx.youtube_digest_service = YoutubeDigestService(
                ctx.ai_client,
                stock_code_repository=ctx.stock_code_repository,
                max_tokens=int(ai_config.max_tokens),
                chunk_chars=max(2000, _yt_budget - 2000),
                logger=ctx.logger,
            )
            if (
                youtube_config.gemini_fallback_enabled
                and ai_config.provider.lower() == "gemini"
                and ai_config.api_key
            ):
                ctx.youtube_gemini_fallback_service = GeminiYoutubeVideoAnalyzerService(
                    api_key=ai_config.api_key,
                    model=ai_config.model,
                    timeout_sec=float(ai_config.timeout_sec),
                    usage_limiter=ctx.ai_usage_limiter,
                    logger=ctx.logger,
                )
            ctx.youtube_digest_task = YoutubeDigestTask(
                channel_repository=ctx.youtube_channel_repository,
                collector=ctx.youtube_transcript_collector,
                digest_service=ctx.youtube_digest_service,
                digest_repository=ctx.youtube_digest_repository,
                notification_service=ctx.notification_service,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                gemini_fallback_service=ctx.youtube_gemini_fallback_service,
            )

        try:
            _ps_cfg = getattr(ctx.full_config, "position_sizing", None) or PositionSizingConfig()
            _rg_cfg = getattr(ctx.full_config, "risk_gate", None) or RiskGateConfig()
            _op_cfg = getattr(ctx.full_config, "order_policy", None) or OrderPolicyConfig()
            ctx.account_snapshot_cache = AccountSnapshotCache(
                broker_api_wrapper=ctx.broker,
                logger=ctx.logger,
                ttl_sec=_ps_cfg.snapshot_ttl_sec if _ps_cfg else 60,
            )
            _operating_profile = str(getattr(ctx.full_config, "operating_profile", "canary"))
            ctx.position_sizing_service = PositionSizingService(
                account_snapshot_cache=ctx.account_snapshot_cache,
                indicator_service=ctx.indicator_service,
                config=_ps_cfg,
                logger=ctx.logger,
                risk_gate_config=_rg_cfg,
                quote_provider=ctx.broker,
                order_policy_config=_op_cfg,
                env=getattr(ctx.broker, "env", None),
                operating_profile=_operating_profile,
                # P0 0-10: live 는 같은 사이클 미체결 BUY 예약 overlay 활성화.
                # backtest 는 BacktestPortfolioLedger 가 예약을 처리하므로 wiring 하지 않는다.
                enable_intracycle_reservations=True,
                pending_buy_exposure_provider=lambda code, exchange=Exchange.KRX: (
                    ctx.order_execution_service.get_pending_buy_exposure(code, exchange)
                    if getattr(ctx, "order_execution_service", None) is not None else 0
                ),
            )
            ctx.risk_gate_service = RiskGateService(
                config=_rg_cfg,
                kill_switch_service=ctx.kill_switch_service,
                account_snapshot_cache=ctx.account_snapshot_cache,
                strategy_risk_provider=ctx.virtual_trade_service,
                logger=ctx.logger,
                env=getattr(ctx.broker, "env", None),
                operator_alert_service=ctx.operator_alert_service,
                market_buy_reference_price_provider=lambda code, exchange: _resolve_market_buy_reference_price(
                    ctx.broker, ctx.logger, code, exchange
                ),
                operating_profile=_operating_profile,
            )
            ctx.execution_flow_service = ExecutionFlowService(
                data_provider=ctx.broker,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
                cache_ttl_sec=_op_cfg.trade_flow_cache_ttl_sec,
                sample_window_sec=_op_cfg.trade_flow_sample_window_sec,
                stock_query_service=ctx.stock_query_service,
            )
            ctx.order_policy_service = OrderPolicyService(
                config=_op_cfg,
                quote_provider=ctx.broker,
                security_info_provider=ctx.broker,
                trade_flow_provider=ctx.execution_flow_service,
                logger=ctx.logger,
                env=getattr(ctx.broker, "env", None),
            )
            ctx.deferred_order_queue = DeferredOrderQueue(ctx.logger)
            _oe_cfg = _config_value(config_dict, "order_execution", {})
            ctx.order_execution_service = OrderExecutionService(
                broker_api_wrapper=ctx.broker,
                logger=ctx.logger, market_clock=ctx.market_clock,
                performance_profiler=ctx.pm,
                notification_service=ctx.notification_service,
                market_calendar_service=ctx._mcs,
                price_subscription_service=getattr(ctx, "price_subscription_service", None),
                virtual_trade_service=ctx.virtual_trade_service,
                kill_switch_service=ctx.kill_switch_service,
                account_snapshot_cache=ctx.account_snapshot_cache,
                risk_gate_service=ctx.risk_gate_service,
                order_policy_service=ctx.order_policy_service,
                data_quality_service=ctx.data_quality_service,
                execution_quality_config=getattr(ctx.full_config, "execution_quality_report", None),
                deferred_order_queue=ctx.deferred_order_queue,
                order_max_retries=_config_value(
                    _oe_cfg,
                    "order_max_retries",
                    OrderExecutionService._ORDER_MAX_RETRIES,
                ),
                order_retry_delay_sec=_config_value(
                    _oe_cfg,
                    "order_retry_delay_sec",
                    OrderExecutionService._ORDER_RETRY_DELAY_SEC,
                ),
                market_mode=getattr(ctx, "market_mode", "domestic"),
            )
            # NOTE: streaming_service.register_handler("signing_notice", ...) is performed by WiringPhase.
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:OrderServices] 초기화 실패: {e}", exc_info=True)
            raise

        try:
            if is_overseas_us:
                ctx.oneil_universe_service = None
                ctx.premium_watchlist_generator_task = None
                ctx.cache_warmup_task = None
                ctx.log_cleanup_task = None
                ctx.newhigh_task = None
                ctx.newhigh_service = None
                ctx.strategy_log_report_task = None
                ctx.post_market_replay_audit_task = None
                ctx.newhigh_strategy_coverage_backtest_task = None
                ctx.after_market_reconcile_task = None
                ctx.opening_position_reconcile_task = None
                ctx.paper_account_expiry_alert_task = None
                ctx.microstructure_capture_task = None
                ctx.theme_intraday_leader_alert_task = None
                if needs_web:
                    ctx.notification_queue_task = NotificationQueueTask(
                        notification_service=ctx.notification_service,
                        poll_interval=config_dict.get("notification_queue_poll_interval", 1.0),
                        telegram_config=getattr(getattr(ctx.full_config, "notifications", None), "telegram", None),
                        logger=ctx.logger,
                    )
                else:
                    ctx.notification_queue_task = None
                # Phase 3c: 해외 VBO dry-run 파이프라인 (주문 경로 없음 — 실주문 불가).
                OverseasBootstrap(ctx).build_dryrun_pipeline()
                return

            ctx.oneil_universe_service = OneilUniverseService(
                stock_query_service=ctx.stock_query_service,
                indicator_service=ctx.indicator_service,
                stock_code_repository=ctx.stock_code_repository,
                market_clock=ctx.market_clock,
                scraper_service=NaverFinanceScraperService(logger=ctx.logger),
                logger=ctx.logger,
                performance_profiler=ctx.pm,
                price_subscription_service=getattr(ctx, "price_subscription_service", None),
                rs_rating_service=getattr(ctx, "rs_rating_service", None),
                minervini_service=getattr(ctx, "minervini_stage_service", None),
                notification_service=getattr(ctx, "notification_service", None),
                classification_repository=getattr(ctx, "theme_classification_repository", None),
            )
            if needs_batch:
                premium_watchlist_ai_service = None
                if ctx.ai_stock_analyzer:
                    premium_watchlist_ai_service = PremiumWatchlistAiAnalysisService(
                        ai_stock_analyzer=ctx.ai_stock_analyzer,
                        favorite_service=ctx.favorite_service,
                        stock_code_repository=ctx.stock_code_repository,
                        stock_query_service=ctx.stock_query_service,
                        minervini_stage_service=getattr(ctx, "minervini_stage_service", None),
                        rs_rating_service=getattr(ctx, "rs_rating_service", None),
                        dart_disclosure_repository=getattr(ctx, "dart_disclosure_repository", None),
                        stock_news_collector=getattr(ctx, "stock_news_collector", None),
                        logger=ctx.logger,
                    )
                ctx.premium_watchlist_generator_task = PremiumWatchlistGeneratorTask(
                    universe_service=ctx.oneil_universe_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    notification_service=ctx.notification_service,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                    telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                    premium_watchlist_ai_service=premium_watchlist_ai_service,
                )
            else:
                ctx.premium_watchlist_generator_task = None

            if needs_trading:
                ctx.cache_warmup_task = CacheWarmupTask(
                    market_data_service=ctx.market_data_service,
                    stock_query_service=ctx.stock_query_service,
                    universe_service=ctx.oneil_universe_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    notification_service=ctx.notification_service,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                )
            else:
                ctx.cache_warmup_task = None

            if needs_batch:
                ctx.log_cleanup_task = LogCleanupTask(
                    log_dir=ctx.logger.log_dir,
                    delete_days=30,
                    compress_days=7,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                )
                ctx.newhigh_task = NewHighTask(
                    stock_repo=ctx.stock_repository,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                    notification_service=ctx.notification_service,
                    daily_price_collector_task=ctx.daily_price_collector_task,
                    stock_query_service=ctx.stock_query_service,
                    rs_rating_service=getattr(ctx, 'rs_rating_service', None),
                    worker_pool=ctx.worker_pool,
                )
                ctx.newhigh_service = NewHighService(
                    stock_repository=ctx.stock_repository,
                    newhigh_task=ctx.newhigh_task,
                    logger=ctx.logger,
                )
                if ctx.theme_classification_repository is not None:
                    ctx.theme_classification_task = ThemeClassificationTask(
                        collector_service=ThemeClassificationCollectorService(
                            classification_repository=ctx.theme_classification_repository,
                            logger=ctx.logger,
                        ),
                        classification_repository=ctx.theme_classification_repository,
                        market_calendar_service=ctx._mcs,
                        market_clock=ctx.market_clock,
                        logger=ctx.logger,
                        worker_pool=ctx.worker_pool,
                    )
                if ctx.theme_daily_leader_service is not None:
                    ctx.theme_daily_leader_report_task = ThemeDailyLeaderReportTask(
                        ranking_task=ctx.ranking_task,
                        theme_daily_leader_service=ctx.theme_daily_leader_service,
                        telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                        notification_service=ctx.notification_service,
                        mcs=ctx._mcs,
                        market_clock=ctx.market_clock,
                        logger=ctx.logger,
                        worker_pool=ctx.worker_pool,
                    )
                ctx.strategy_log_report_task = StrategyLogReportTask(
                    report_service=StrategyLogReportService(
                        log_dir=os.path.join(ctx.logger.log_dir, "strategies"),
                        stock_code_repo=ctx.stock_code_repository,
                        virtual_trade_service=ctx.virtual_trade_service,
                        backtest_journal_provider=ctx.backtest_journal_repository.load_records_for_date,
                        execution_quality_config=getattr(ctx.full_config, "execution_quality_report", None),
                        strategy_degradation_config=getattr(
                            ctx.full_config, "strategy_performance_degradation", None
                        ),
                        profitability_gate_config=getattr(
                            ctx.full_config, "strategy_profitability_gate", None
                        ),
                        enabled_strategy_provider=ctx._get_enabled_strategy_names_for_report,
                    ),
                    notification_service=ctx.notification_service,
                    operator_alert_service=ctx.operator_alert_service,
                    kill_switch_service=ctx.kill_switch_service,
                    telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                    mcs=ctx._mcs,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                    rejection_distribution_service=ctx.rejection_distribution_service,
                )
                BacktestTaskBootstrap(ctx).run()
                ctx.after_market_reconcile_task = AfterMarketReconcileTask(
                    order_execution_service=ctx.order_execution_service,
                    notification_service=ctx.notification_service,
                    operator_alert_service=ctx.operator_alert_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    logger=ctx.logger,
                    worker_pool=ctx.worker_pool,
                )
                # todo 1-5: 장마감 후 후보/보유 종목 microstructure overlay 캡처 (replay 코퍼스 축적)
                microstructure_enabled = config_dict.get("microstructure_capture_enabled", True)
                ctx.microstructure_capture_task = MicrostructureCaptureTask(
                    capture_service=BacktestMicrostructureCaptureService(
                        stock_query_service=ctx.stock_query_service,
                        program_provider=ctx.broker,
                    ),
                    universe_service=ctx.oneil_universe_service,
                    virtual_trade_service=ctx.virtual_trade_service,
                    stock_query_service=ctx.stock_query_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    scheduler_store=StrategySchedulerStore(logger=ctx.logger),
                    logger=ctx.logger,
                    notification_service=ctx.notification_service,
                ) if microstructure_enabled else None
                # todo 1-5: 장중 캡처 후보 프로그램매매 WS 구독 (pt_history 장중 시계열 축적).
                # LOW 우선순위 — 트레이딩용 price 구독을 밀어내지 않는다.
                program_capture_sub_enabled = config_dict.get(
                    "program_capture_subscription_enabled", True
                )
                ctx.program_capture_subscription_task = ProgramCaptureSubscriptionTask(
                    subscription_policy=ctx.price_subscription_service,
                    streaming_stock_repo=ctx.streaming_stock_repo,
                    universe_service=ctx.oneil_universe_service,
                    virtual_trade_service=ctx.virtual_trade_service,
                    stock_query_service=ctx.stock_query_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    scheduler_store=StrategySchedulerStore(logger=ctx.logger),
                    max_codes=config_dict.get("program_capture_rotation_batch_size", 10),
                    rotation_interval_minutes=config_dict.get(
                        "program_capture_rotation_interval_minutes", 30
                    ),
                    logger=ctx.logger,
                ) if (
                    program_capture_sub_enabled and ctx.price_subscription_service
                ) else None
            else:
                ctx.log_cleanup_task = None
                ctx.newhigh_task = None
                ctx.newhigh_service = None
                ctx.theme_classification_task = None
                ctx.theme_daily_leader_report_task = None
                ctx.strategy_log_report_task = None
                ctx.post_market_replay_audit_task = None
                ctx.newhigh_strategy_coverage_backtest_task = None
                ctx.after_market_reconcile_task = None
                ctx.microstructure_capture_task = None
                ctx.program_capture_subscription_task = None
                ctx.theme_intraday_leader_alert_task = None

            ctx.theme_intraday_leader_alert_task = ThemeIntradayLeaderAlertTask(
                ranking_task=ctx.ranking_task,
                theme_daily_leader_service=ctx.theme_daily_leader_service,
                telegram_reporter=getattr(ctx, 'telegram_reporter', None),
                market_calendar_service=ctx._mcs,
                market_clock=ctx.market_clock,
                logger=ctx.logger,
            ) if (
                needs_trading
                and ctx.ranking_task is not None
                and ctx.theme_daily_leader_service is not None
            ) else None

            index_alert_cfg = config_dict.get("market_index_alert", {})
            index_alert_enabled = (
                index_alert_cfg.get("enabled", True)
                if isinstance(index_alert_cfg, dict) else True
            )
            ctx.market_index_threshold_alert_task = MarketIndexThresholdAlertTask(
                broker=ctx.broker,
                market_status_alert_service=ctx.market_status_alert_service,
                market_clock=ctx.market_clock,
                market_calendar_service=ctx._mcs,
                check_interval_sec=index_alert_cfg.get("poll_interval_sec", 60),
                logger=ctx.logger,
            ) if (
                needs_trading
                and index_alert_enabled
                and ctx.market_status_alert_service is not None
            ) else None
            ctx.market_timing_daily_update_task = MarketTimingDailyUpdateTask(
                universe_service=ctx.oneil_universe_service,
                market_clock=ctx.market_clock,
                market_calendar_service=ctx._mcs,
                logger=ctx.logger,
            ) if (
                needs_trading
                and ctx.oneil_universe_service is not None
            ) else None

            OverseasBootstrap(ctx).build_favorite_price_alert(config_dict, needs_web=needs_web)

            if needs_web:
                ctx.notification_queue_task = NotificationQueueTask(
                    notification_service=ctx.notification_service,
                    poll_interval=config_dict.get("notification_queue_poll_interval", 1.0),
                    telegram_config=getattr(getattr(ctx.full_config, "notifications", None), "telegram", None),
                    logger=ctx.logger,
                )
            else:
                ctx.notification_queue_task = None

            reconcile_cfg = getattr(ctx.full_config, "opening_position_reconcile", None)
            ctx.opening_position_reconcile_task = None
            if needs_trading and (reconcile_cfg is None or getattr(reconcile_cfg, "enabled", True)):
                deprecated_reconcile_keys = (
                    "detect_only",
                    "auto_buy_missing_local",
                    "auto_sell_extra_broker",
                    "allow_sell_unknown_broker",
                )
                reconcile_extra = getattr(reconcile_cfg, "model_extra", {}) or {}
                for key in deprecated_reconcile_keys:
                    if key in reconcile_extra:
                        ctx.logger.warning(
                            f"opening_position_reconcile.{key} is deprecated and ignored"
                        )
                opening_reconcile_service = OpeningPositionReconcileService(
                    broker=ctx.broker,
                    virtual_trade_service=ctx.virtual_trade_service,
                    market_clock=ctx.market_clock,
                    is_paper_trading=bool(getattr(ctx.env, "is_paper_trading", False)),
                    paper_account_number=getattr(ctx.env, "paper_stock_account_number", None),
                    paper_account_rollover_state_file_path=getattr(
                        reconcile_cfg,
                        "paper_account_rollover_state_file_path",
                        "data/paper_account_reconcile_state.json",
                    ),
                    block_force_close_on_paper_rollover=getattr(
                        reconcile_cfg,
                        "block_force_close_on_paper_rollover",
                        True,
                    ),
                    logger=ctx.logger,
                )
                ctx.opening_position_reconcile_task = OpeningPositionReconcileTask(
                    reconcile_service=opening_reconcile_service,
                    market_calendar_service=ctx._mcs,
                    market_clock=ctx.market_clock,
                    notification_service=ctx.notification_service,
                    operator_alert_service=ctx.operator_alert_service,
                    logger=ctx.logger,
                    check_interval_sec=getattr(reconcile_cfg, "check_interval_sec", 30),
                    open_delay_sec=getattr(reconcile_cfg, "open_delay_sec", 60),
                    run_window_min=getattr(reconcile_cfg, "run_window_min", 10),
                )

            # 해외 VBO dry-run 공존: active=domestic 이라도 enabled_market_modes 에
            # overseas_us 가 포함되면 국내 active 런과 함께 조립한다. dry-run 태스크는
            # 미국 정규장 마감(16:30 ET) cron 으로 자체 트리거되므로 한국장 배치와
            # 타임존이 충돌하지 않는다(주문 경로 없음 — 실주문 불가).
            if is_market_enabled(ctx, "overseas_us"):
                OverseasBootstrap(ctx).build_dryrun_pipeline()
            else:
                ctx.overseas_candidate_service = None
                ctx.overseas_vbo_dryrun_service = None
                ctx.overseas_pp_dryrun_service = None
                ctx.overseas_bgu_dryrun_service = None
                ctx.overseas_cb_dryrun_service = None
                ctx.overseas_dryrun_task = None
                ctx.overseas_manual_order_service = None
                ctx.overseas_trade_repository = None
                ctx.overseas_fill_reconcile_service = None
        except Exception as e:
            ctx.logger.critical(f"[ServiceBootstrap:Universe] 초기화 실패: {e}", exc_info=True)
            raise
