"""
웹 애플리케이션용 서비스 초기화 모듈.
TradingApp의 초기화 로직을 참고하여 서비스 레이어만 초기화한다.
"""
import asyncio
import time
from config.config_loader import load_configs, KillSwitchConfig, RiskGateConfig, DataQualityConfig
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
import os
from brokers.broker_api_wrapper import BrokerAPIWrapper
from services.stock_query_service import StockQueryService
from services.streaming_service import StreamingService
from services.order_execution_service import OrderExecutionService
from services.deferred_order_queue import DeferredOrderQueue
from repositories.virtual_trade_repository import VirtualTradeRepository
from repositories.backtest_journal_repository import BacktestJournalRepository
from services.virtual_trade_service import VirtualTradeService
from repositories.stock_code_repository import StockCodeRepository
from repositories.rs_rating_repository import RSRatingRepository
from repositories.favorite_repository import FavoriteRepository
from services.favorite_service import FavoriteService
from services.indicator_service import IndicatorService
from core.market_clock import MarketClock
from core.logger import Logger, get_strategy_logger, get_streaming_logger
from core.performance_profiler import PerformanceProfiler
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from scheduler.background_scheduler import BackgroundScheduler
from scheduler.foreground_scheduler import ForegroundScheduler
from scheduler.ticket_queue.message_broker import MessageBroker
from scheduler.ticket_queue.dlq_manager import DlqManager
from scheduler.worker.worker_pool import WorkerPool
from scheduler.dispatcher.time_dispatcher import TimeDispatcher
from config.task_config_loader import load_after_market_delays
from interfaces.schedulable_task import TaskPriority
from task.background.intraday.strategy_scheduler_task_adapter import StrategySchedulerTaskAdapter
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask
from task.background.intraday.pre_market_health_check_task import PreMarketHealthCheckTask
from task.background.intraday.opening_position_reconcile_task import OpeningPositionReconcileTask
from task.background.after_market.ranking_task import RankingTask
from task.background.after_market.minervini_update_task import MinerviniUpdateTask
from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from task.background.after_market.premium_watchlist_generator_task import PremiumWatchlistGeneratorTask
from task.background.after_market.cache_warmup_task import CacheWarmupTask
from task.background.after_market.log_cleanup_task import LogCleanupTask
from task.background.after_market.newhigh_task import NewHighTask
from task.background.after_market.strategy_log_report_task import StrategyLogReportTask
from task.background.after_market.after_market_reconcile_task import AfterMarketReconcileTask
from services.strategy_log_report_service import StrategyLogReportService, _REASON_KR
from services.rejection_distribution_service import RejectionDistributionService
from task.background.always_on.notification_queue_task import NotificationQueueTask
from services.naver_finance_scraper_service import NaverFinanceScraperService
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
from strategies.larry_williams_channel_breakout_strategy import LarryWilliamsChannelBreakoutStrategy
from services.oneil_universe_service import OneilUniverseService
from repositories.stock_repository import StockRepository
from services.program_trading_stream_service import ProgramTradingStreamService
from services.market_data_service import MarketDataService
from services.market_calendar_service import MarketCalendarService
from services.notification_service import NotificationService, NotificationCategory
from services.kill_switch_service import KillSwitchService
from services.operator_alert_service import OperatorAlertService
from core.account_snapshot import AccountSnapshotCache
from core.retry_queue.api_budget_limiter import ApiBudgetLimiter
from services.position_sizing_service import PositionSizingService
from services.risk_gate_service import RiskGateService
from services.order_policy_service import OrderPolicyService
from services.execution_flow_service import ExecutionFlowService
from config.config_loader import PositionSizingConfig, OrderPolicyConfig
from services.price_subscription_service import PriceSubscriptionService
from services.price_stream_service import PriceStreamService
from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType
from services.telegram_notifier import TelegramNotifier, TelegramReporter
from services.data_quality_service import DataQualityService
from services.opening_position_reconcile_service import OpeningPositionReconcileService

from core.cache.cache_store import CacheStore
from services.rs_rating_service import RSRatingService
from services.minervini_stage_service import MinerviniStageService
from services.newhigh_service import NewHighService
from common.types import ErrorCode

class WebAppContext:
    """웹 앱에서 사용할 서비스 컨텍스트."""

    REST_PRICE_REFRESH_COOLDOWN_SEC = 10.0

    def __init__(self, app_context, runtime_mode=None):
        from view.web.bootstrap.runtime_mode import RuntimeMode
        self.logger = Logger()
        self.runtime_mode: RuntimeMode = runtime_mode if runtime_mode is not None else RuntimeMode.ALL
        self.env = app_context.env if app_context else None
        self.full_config = {}  # [추가] 전체 설정을 담을 그릇
        self.market_clock: MarketClock = None
        self.broker: BrokerAPIWrapper = None
        self.stock_query_service: StockQueryService = None
        self.streaming_service: StreamingService = None
        self.order_execution_service: OrderExecutionService = None
        self.kill_switch_service: KillSwitchService = None
        self.operator_alert_service: OperatorAlertService = None
        self.rejection_distribution_service: RejectionDistributionService = None
        self.data_quality_service: DataQualityService = None
        self.indicator_service: IndicatorService = None
        self.virtual_repo = VirtualTradeRepository()
        self.backtest_journal_repository = BacktestJournalRepository()
        self.virtual_trade_service = VirtualTradeService(repository=self.virtual_repo, market_clock=self.market_clock)
        self.virtual_trade_service.backfill_snapshots()  # 과거 CSV 기반 스냅샷 역산
        self.stock_code_repository = StockCodeRepository(logger=self.logger)
        self.favorite_repo = FavoriteRepository()
        self.favorite_service = FavoriteService(
            repository=self.favorite_repo,
            stock_code_repository=self.stock_code_repository,
        )
        self.account_snapshot_cache: AccountSnapshotCache = None
        self.api_budget_limiter = ApiBudgetLimiter()
        self.risk_gate_service: RiskGateService = None
        self.order_policy_service: OrderPolicyService = None
        self.execution_flow_service: ExecutionFlowService = None
        self.position_sizing_service: PositionSizingService = None
        self.scheduler: StrategyScheduler = None
        self.oneil_universe_service: OneilUniverseService = None
        self.ranking_task: RankingTask = None
        self.minervini_update_task: MinerviniUpdateTask = None
        self.websocket_watchdog_task: WebSocketWatchdogTask = None
        self.pre_market_health_check_task: PreMarketHealthCheckTask = None
        self.opening_position_reconcile_task: OpeningPositionReconcileTask = None
        self.after_market_reconcile_task: AfterMarketReconcileTask = None
        self.daily_price_collector_task: DailyPriceCollectorTask = None
        self.ohlcv_update_task: OhlcvUpdateTask = None
        self.premium_watchlist_generator_task: PremiumWatchlistGeneratorTask = None
        self.cache_warmup_task: CacheWarmupTask = None
        self.log_cleanup_task: LogCleanupTask = None
        self.newhigh_task: NewHighTask = None
        self.strategy_log_report_task: StrategyLogReportTask = None
        self.stock_repository: StockRepository = None
        self.background_scheduler: BackgroundScheduler = None
        self.foreground_scheduler: ForegroundScheduler = None
        self._mcs: MarketCalendarService = None
        self.notification_service: NotificationService = None
        self.notification_queue_task: NotificationQueueTask = None
        self.initialized = False
        self.pm: PerformanceProfiler = None

        # 프로그램매매 실시간 데이터 서비스
        self.program_trading_stream_service = ProgramTradingStreamService(self.logger)
        self.price_subscription_service: PriceSubscriptionService = None
        self.price_stream_service: PriceStreamService = None
        self.streaming_stock_repo: StreamingStockRepo = None
        # P2 2-4: event-driven shadow 인프라 (default: 비활성, event_driven_shadow=False)
        self.strategy_event_router = None
        self.event_shadow_journal_service = None
        self._last_missing_reason_log_ts: dict[tuple[str, str], float] = {}
        self._last_rest_price_refresh_ts: dict[str, float] = {}
        self._pending_rest_price_refresh_tasks: dict[str, asyncio.Task] = {}

        # 실시간 스트리밍 전용 이벤트 로거 (logs/streaming/)
        self.streaming_event_logger = get_streaming_logger()

    def load_config_and_env(self):
        """설정 파일 로드 및 환경 초기화. ConfigBootstrap 에 위임."""
        from view.web.bootstrap.config_bootstrap import ConfigBootstrap
        ConfigBootstrap(self).run()

    _POSITION_SIZING_STATE_FILE = "data/position_sizing_state.json"

    def _load_position_sizing_state(self) -> None:
        """data/position_sizing_state.json 이 있으면 in-memory config 에 반영한다."""
        import json as _json, os as _os
        if not _os.path.exists(self._POSITION_SIZING_STATE_FILE):
            return
        try:
            with open(self._POSITION_SIZING_STATE_FILE, "r", encoding="utf-8") as f:
                state = _json.load(f)
            rg = getattr(self.full_config, "risk_gate", None)
            ps = getattr(self.full_config, "position_sizing", None)
            if rg is not None and "max_order_amount_won" in state and state["max_order_amount_won"] is not None:
                rg.max_order_amount_won = int(state["max_order_amount_won"])
            if ps is not None and "max_per_position_pct" in state and state["max_per_position_pct"] is not None:
                ps.max_per_position_pct = float(state["max_per_position_pct"])
            self.logger.info(f"[PositionSizingState] 로드 완료: {state}")
        except Exception as e:
            self.logger.warning(f"[PositionSizingState] 로드 실패 (무시): {e}")

    def save_position_sizing_state(self) -> None:
        """현재 max_order_amount_won / max_per_position_pct 를 JSON 파일로 저장한다."""
        import json as _json, os as _os
        from datetime import datetime, timezone as _tz
        try:
            rg = getattr(self.full_config, "risk_gate", None)
            ps = getattr(self.full_config, "position_sizing", None)
            state = {
                "max_order_amount_won": rg.max_order_amount_won if rg else None,
                "max_per_position_pct": ps.max_per_position_pct if ps else None,
                "updated_at": datetime.now(tz=_tz.utc).astimezone().isoformat(),
            }
            _os.makedirs("data", exist_ok=True)
            with open(self._POSITION_SIZING_STATE_FILE, "w", encoding="utf-8") as f:
                _json.dump(state, f, ensure_ascii=False)
        except Exception as e:
            self.logger.warning(f"[PositionSizingState] 저장 실패: {e}")

    async def initialize_services(self, is_paper_trading: bool = True):
        """서비스 레이어 초기화."""
        self.env.set_trading_mode(is_paper_trading)
        if not await self._bootstrap_broker(is_paper_trading):
            return False
        try:
            self._bootstrap_services()
            self._bootstrap_schedulers()
        except Exception as e:
            self.logger.critical(f"웹 앱: 서비스 초기화 실패: {e}", exc_info=True)
            return False
        self.initialized = True
        mode = "모의투자" if is_paper_trading else "실전투자"
        self.logger.info(f"웹 앱: 서비스 초기화 완료 ({mode})")
        return True

    async def _bootstrap_broker(self, is_paper_trading: bool) -> bool:
        """토큰 발급 및 BrokerAPIWrapper 초기화. BrokerBootstrap 에 위임."""
        from view.web.bootstrap.broker_bootstrap import BrokerBootstrap
        return await BrokerBootstrap(self).run(is_paper_trading)

    def _bootstrap_services(self):
        """서비스 레이어 초기화. ServiceContainer → WiringPhase 순서로 위임."""
        from view.web.bootstrap.service_container import ServiceContainer
        from view.web.bootstrap.wiring_phase import WiringPhase
        ServiceContainer(self).run()
        WiringPhase(self).run()

    def _bootstrap_schedulers(self):
        """스케줄러 인프라 초기화. SchedulerBootstrap 에 위임."""
        from view.web.bootstrap.scheduler_bootstrap import SchedulerBootstrap
        SchedulerBootstrap(self).run()

    async def _initialize_price_subscriptions(self) -> None:
        """기동 시 포트폴리오(HIGH) 및 프리미엄 종목(MEDIUM) 구독을 초기화."""
        if not self.price_subscription_service:
            return

        from services.price_subscription_service import SubscriptionPriority

        # 1. 보유 종목 → HIGH 구독 (전략 스케줄러의 가상 보유 종목 기준)
        try:
            holdings = self.virtual_trade_service.get_holds() if self.virtual_trade_service else []
            for item in holdings:
                code = item.get("code", "").strip()
                if code:
                    await self.price_subscription_service.add_subscription(
                        code, SubscriptionPriority.HIGH, "portfolio", StreamingType.UNIFIED_PRICE
                    )
        except Exception as e:
            self.logger.warning(f"보유 종목 구독 초기화 실패: {e}")

        # 2. 프리미엄 종목 → MEDIUM 구독
        try:
            import json, os
            premium_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "premium_stocks.json")
            if os.path.exists(premium_path):
                with open(premium_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("kospi", []) + data.get("kosdaq", [])
                codes = [
                    item["code"] if isinstance(item, dict) else item
                    for item in raw
                    if (item.get("code") if isinstance(item, dict) else item)
                ]
                if codes:
                    await self.price_subscription_service.sync_subscriptions(
                        codes=codes,
                        category_key="strategy_premium",
                        priority=SubscriptionPriority.MEDIUM,
                    )
        except Exception as e:
            self.logger.warning(f"프리미엄 종목 구독 초기화 실패: {e}")

    def get_env_type(self) -> str:
        if self.env is None:
            return "미설정"
        return "모의투자" if self.env.is_paper_trading else "실전투자"

    async def is_market_open_now(self) -> bool:
        if self._mcs is None:
            return False
        return await self._mcs.is_market_open_now() if self._mcs else False

    def get_current_time_str(self) -> str:
        if self.market_clock is None:
            return ""
        return self.market_clock.get_current_kst_time().strftime('%Y-%m-%d %H:%M:%S')

    def get_cache_stats(self, expand: bool = False, latest_trading_date: str = None) -> dict:
        """메모리 캐시 통계를 반환합니다."""
        if self.stock_repository:
            return self.stock_repository.get_cache_stats(expand=expand, latest_trading_date=latest_trading_date)
        return {}

    # --- 전략 스케줄러 ---

    def _get_enabled_strategy_names_for_report(self):
        scheduler = getattr(self, "scheduler", None)
        if not scheduler:
            return None
        strategies = getattr(scheduler, "_strategies", [])
        return [
            cfg.strategy.name
            for cfg in strategies
            if getattr(cfg, "enabled", False)
        ]

    def initialize_scheduler(self):
        """전략 스케줄러 생성 및 전략 등록. StrategyFactory 에 위임."""
        from view.web.bootstrap.strategy_factory import StrategyFactory
        StrategyFactory(self).build()

    async def ensure_strategy_states_loaded(self):
        """등록된 전략 중 `load_state()` 를 가진 전략의 state 를 명시적으로 await 로드한다.

        StrategyFactory.build() 직후 + scheduler.start() 전에 호출. 기존 `__init__` 의
        fire-and-forget `_load_state()` 가 scan 보다 늦게 끝나는 race 를 제거한다.

        실패 정책 (P0 0-1 의 RiskGateFailOpenConfig 패턴과 동일):
        - paper: fail-OPEN — load 실패해도 error log 만 남기고 계속 진행한다.
          모의 환경에서는 stale state 위험보다 개발 흐름 유지가 우선.
        - real: fail-CLOSE — 한 전략이라도 실패하면 RuntimeError 를 raise 한다.
          실전에서는 stale/유실 state 로 신규 주문이 나가는 위험이 더 크다.
        - env 미설정: 보수적으로 fail-OPEN 동작.
        """
        if self.scheduler is None:
            return
        is_paper = True
        if self.env is not None:
            is_paper = bool(getattr(self.env, "is_paper_trading", True))
        failed: list[tuple[str, BaseException]] = []
        for cfg in getattr(self.scheduler, "_strategies", []):
            strategy = getattr(cfg, "strategy", None)
            load_fn = getattr(strategy, "load_state", None)
            if load_fn is None:
                continue
            try:
                await load_fn()
            except Exception as exc:
                name = getattr(strategy, "name", "?")
                if self.logger:
                    self.logger.error(
                        f"[WebAppContext] strategy.load_state() 실패 ({name}): {exc}"
                    )
                failed.append((name, exc))
        if failed and not is_paper:
            names = ", ".join(name for name, _ in failed)
            raise RuntimeError(
                f"실전 모드 bootstrap 차단: 전략 state load 실패 — {names}"
            )

    def start_background_tasks(self):
        """백그라운드 태스크 시작 — BackgroundScheduler에 위임."""
        # StreamingService에 콜백 등록 (내부 저장 → 재연결 시에도 자동 유지됨)
        if self.streaming_service:
            self.streaming_service._callback = self._web_realtime_callback

        if self.background_scheduler:
            asyncio.create_task(self.background_scheduler.start_all())

    async def shutdown(self):
        """서비스 종료 처리 — BackgroundScheduler에 위임."""
        pending_tasks = list(self._pending_rest_price_refresh_tasks.values())
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._pending_rest_price_refresh_tasks.clear()
        if self.background_scheduler:
            await self.background_scheduler.shutdown()
        if self.broker:
            await self.broker.stop()
        self.logger.info("웹 앱: 서비스 종료 완료")

    # --- 프로그램매매 실시간 스트리밍 ---

    def _web_realtime_callback(self, data):
        """웹소켓 실시간 콜백: 가격 주입 후 StreamingService 중앙 디스패처에 전달."""
        if data.get('type') == 'realtime_program_trading':
            item = data.get('data', {})
            # 현재가 정보 주입 — dispatch 전에 수행해야 핸들러가 가격 정보를 수신
            if self.streaming_service:
                code = item.get('유가증권단축종목코드')
                price_data = self.streaming_service.get_cached_realtime_price(code)
                if price_data:
                    if isinstance(price_data, dict):
                        item['price'] = price_data.get('price')
                        item['change'] = price_data.get('change')
                        item['rate'] = price_data.get('rate')
                        item['sign'] = price_data.get('sign')
                    else:
                        item['price'] = price_data
                elif code:
                    self._log_streaming_missing_reason(code)
                    self._schedule_rest_price_refresh(code)

        if self.streaming_service:
            self.streaming_service.dispatch_realtime_message(data)

    def _emit_missing_reason(self, code: str, reason: str) -> None:
        """동일 종목/원인 로그를 저빈도로 남긴다."""
        if not self.streaming_event_logger:
            return

        now_ts = time.monotonic()
        last_logged_ts = self._last_missing_reason_log_ts.get((code, reason), 0.0)
        if (now_ts - last_logged_ts) < 60.0:
            return

        self._last_missing_reason_log_ts[(code, reason)] = now_ts
        self.streaming_event_logger.log_missing_reason(code, reason)

    def _log_streaming_missing_reason(self, code: str) -> None:
        """PT 이벤트에 비해 체결가가 없는 상황의 원인을 저빈도로 기록한다."""
        if not self.streaming_event_logger or not self.streaming_service:
            return

        if not self.streaming_service.is_subscribed_realtime_price(code):
            reason = "not_subscribed"
        elif self.price_stream_service:
            subscription_age = self.price_stream_service.get_subscription_age(code)
            if subscription_age < WebSocketWatchdogTask.PRICE_SUBSCRIPTION_GRACE_SEC:
                return
            reason = "subscribed_no_tick"
        else:
            reason = "subscribed_no_tick"

        self._emit_missing_reason(code, reason)

    def _schedule_rest_price_refresh(self, code: str) -> None:
        """PT 수신은 왔는데 체결가 캐시가 비어 있을 때 REST 스냅샷 보강을 예약한다."""
        if (
            not code
            or not self.stock_query_service
            or not self.price_stream_service
            or not self.streaming_service
            or not self.streaming_service.is_subscribed_realtime_price(code)
        ):
            return

        existing_task = self._pending_rest_price_refresh_tasks.get(code)
        if existing_task and not existing_task.done():
            return

        now_ts = time.monotonic()
        last_refresh_ts = self._last_rest_price_refresh_ts.get(code, 0.0)
        if (now_ts - last_refresh_ts) < self.REST_PRICE_REFRESH_COOLDOWN_SEC:
            return

        self._last_rest_price_refresh_ts[code] = now_ts
        task = asyncio.create_task(self._refresh_price_from_rest(code))
        self._pending_rest_price_refresh_tasks[code] = task

        def _cleanup(done_task: asyncio.Task, stock_code: str = code) -> None:
            if self._pending_rest_price_refresh_tasks.get(stock_code) is done_task:
                self._pending_rest_price_refresh_tasks.pop(stock_code, None)

        task.add_done_callback(_cleanup)

    async def _refresh_price_from_rest(self, code: str) -> None:
        """REST 현재가 조회로 가격 캐시를 보강하고 실패 시 원인을 기록한다."""
        try:
            resp = await self.stock_query_service.get_current_price(
                code,
                caller="WebAppContext",
                count_stats=False,
                force_fresh=True,
            )
        except Exception as e:
            self.logger.warning(f"PT 가격 보강용 REST 조회 예외 ({code}): {e}", exc_info=True)
            self._emit_missing_reason(code, "rest_failed")
            return

        quality = None
        if self.data_quality_service:
            quality = self.data_quality_service.validate_api_response(resp, code=code, require_output=True)
        if quality is not None and not quality.ok:
            self._emit_missing_reason(code, quality.reason)
            return

        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            self._emit_missing_reason(code, "rest_failed")
            return

        output = resp.data.get("output") if isinstance(resp.data, dict) else getattr(resp.data, "output", None)
        if not output:
            self._emit_missing_reason(code, "rest_invalid")
            return

        def _get(field_name: str, default: str = ""):
            value = output.get(field_name) if isinstance(output, dict) else getattr(output, field_name, None)
            if value is None or value == "":
                return default
            return str(value)

        price = _get("stck_prpr")
        if not price:
            self._emit_missing_reason(code, "rest_invalid")
            return

        self.price_stream_service.cache_price_snapshot(
            code,
            price=price,
            change=_get("prdy_vrss", "0"),
            rate=_get("prdy_ctrt", "0.00"),
            sign=_get("prdy_vrss_sign", "3"),
            volume=_get("acml_vol", "0"),
        )

    async def start_program_trading(self, code: str) -> bool:
        """프로그램매매 구독 시작 (웹소켓 연결 + 구독). 이미 구독 중이면 스킵."""
        pt_desired = (
            self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)
            if self.streaming_stock_repo else set()
        )
        if code in pt_desired:
            # 구독 상태이지만 수신 태스크가 죽었으면 강제 재연결
            if (self.broker
                    and not self.broker.is_websocket_receive_alive()):
                self.logger.warning(f"[프로그램매매] {code} 구독 상태이나 수신 태스크 종료됨. 재연결 시도.")
                await self.websocket_watchdog_task.force_reconnect_program_trading()

                # 재연결 후에도 desired에 있으면 성공으로 간주
                if self.streaming_stock_repo and code in self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING):
                    return True
                self.logger.info(f"[프로그램매매] {code} 재연결 실패로 구독 해제됨. 신규 구독 재시도.")
            else:
                return True

        try:
            t_start = self.pm.start_timer()
            connected = await self.streaming_service.connect_websocket(self._web_realtime_callback)
            self.pm.log_timer(f"connect_websocket({code})", t_start)
            if not connected:
                self.logger.warning(f"프로그램매매 구독 실패 (WebSocket 연결 불가): {code}")
                return False

            t_sub_pt = self.pm.start_timer()
            sub_pt_success = await self.streaming_service.subscribe_program_trading(code)
            self.pm.log_timer(f"subscribe_program_trading({code})", t_sub_pt)

            t_sub_price = self.pm.start_timer()
            sub_price_success = await self.streaming_service.subscribe_unified_price(code)
            self.pm.log_timer(f"subscribe_unified_price({code})", t_sub_price)

            if sub_pt_success and sub_price_success:
                if self.streaming_stock_repo:
                    await self.streaming_stock_repo.mark_desired(code, StreamingType.PROGRAM_TRADING)
                    await self.streaming_stock_repo.mark_active(code, StreamingType.PROGRAM_TRADING)
                self.logger.info(f"프로그램매매 신규 구독 성공: {code}")
                return True
            else:
                # 하나라도 실패하면, 성공했을 수 있는 다른 구독을 해지하여 상태를 정리한다.
                self.logger.warning(f"프로그램매매 구독 실패 (pt: {sub_pt_success}, price: {sub_price_success}) - {code}")
                if sub_pt_success:
                    await self.streaming_service.unsubscribe_program_trading(code)
                if sub_price_success:
                    await self.streaming_service.unsubscribe_unified_price(code)
                return False

        except Exception as e:
            self.logger.error(f"프로그램매매 구독 중 예외 발생 ({code}): {e}", exc_info=True)
            return False

    async def stop_program_trading(self, code: str):
        """특정 종목 프로그램매매 구독 해지."""
        if self.streaming_stock_repo and code in self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING):
            await self.streaming_service.unsubscribe_program_trading(code)
            await self.streaming_service.unsubscribe_unified_price(code)
            await self.streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
            await self.streaming_stock_repo.mark_inactive(code, StreamingType.PROGRAM_TRADING)

    async def stop_all_program_trading(self):
        """모든 프로그램매매 구독 해지."""
        codes = sorted(self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) if self.streaming_stock_repo else []
        for code in codes:
            await self.streaming_service.unsubscribe_program_trading(code)
            await self.streaming_service.unsubscribe_unified_price(code)
            if self.streaming_stock_repo:
                await self.streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
                await self.streaming_stock_repo.mark_inactive(code, StreamingType.PROGRAM_TRADING)
