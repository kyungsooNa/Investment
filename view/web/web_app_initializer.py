"""
웹 애플리케이션용 서비스 초기화 모듈.
TradingApp의 초기화 로직을 참고하여 서비스 레이어만 초기화한다.
"""
import asyncio
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
import os
from brokers.broker_api_wrapper import BrokerAPIWrapper
from services.stock_query_service import StockQueryService
from services.streaming_service import StreamingService
from services.order_execution_service import OrderExecutionService
from repositories.virtual_trade_repository import VirtualTradeRepository
from services.virtual_trade_service import VirtualTradeService
from repositories.stock_code_repository import StockCodeRepository
from repositories.favorite_repository import FavoriteRepository
from services.favorite_service import FavoriteService
from services.indicator_service import IndicatorService
from core.market_clock import MarketClock
from core.logger import Logger, get_strategy_logger, get_streaming_logger
from core.performance_profiler import PerformanceProfiler
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from scheduler.background_scheduler import BackgroundScheduler
from scheduler.foreground_scheduler import ForegroundScheduler
from task.background.intraday.strategy_scheduler_task_adapter import StrategySchedulerTaskAdapter
from task.background.intraday.websocket_watchdog_task import WebSocketWatchdogTask
from task.background.after_market.ranking_task import RankingTask
from task.background.after_market.daily_price_collector_task import DailyPriceCollectorTask
from task.background.after_market.ohlcv_update_task import OhlcvUpdateTask
from task.background.after_market.premium_watchlist_generator_task import PremiumWatchlistGeneratorTask
from task.background.after_market.cache_warmup_task import CacheWarmupTask
from task.background.after_market.log_cleanup_task import LogCleanupTask
from task.background.always_on.notification_queue_task import NotificationQueueTask
from services.naver_finance_scraper_service import NaverFinanceScraperService
from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy
from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.first_pullback_strategy import FirstPullbackStrategy
from services.oneil_universe_service import OneilUniverseService
from repositories.stock_repository import StockRepository
from services.program_trading_stream_service import ProgramTradingStreamService
from services.market_data_service import MarketDataService
from services.market_calendar_service import MarketCalendarService
from services.notification_service import NotificationService, NotificationCategory
from services.price_subscription_service import PriceSubscriptionService
from services.price_stream_service import PriceStreamService
from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType
from services.telegram_notifier import TelegramNotifier, TelegramReporter
from view.web import web_api  # 임포트 확인
from core.cache.cache_store import CacheStore

class WebAppContext:
    """웹 앱에서 사용할 서비스 컨텍스트."""

    def __init__(self, app_context):
        self.logger = Logger()
        self.env = app_context.env if app_context else None
        self.full_config = {}  # [추가] 전체 설정을 담을 그릇
        self.market_clock: MarketClock = None
        self.broker: BrokerAPIWrapper = None
        self.stock_query_service: StockQueryService = None
        self.streaming_service: StreamingService = None
        self.order_execution_service: OrderExecutionService = None
        self.indicator_service: IndicatorService = None
        self.virtual_repo = VirtualTradeRepository()
        self.virtual_trade_service = VirtualTradeService(repository=self.virtual_repo, market_clock=self.market_clock)
        self.virtual_trade_service.backfill_snapshots()  # 과거 CSV 기반 스냅샷 역산
        self.stock_code_repository = StockCodeRepository(logger=self.logger)
        self.favorite_repo = FavoriteRepository()
        self.favorite_service = FavoriteService(
            repository=self.favorite_repo,
            stock_code_repository=self.stock_code_repository,
        )
        self.scheduler: StrategyScheduler = None
        self.oneil_universe_service: OneilUniverseService = None
        self.ranking_task: RankingTask = None
        self.websocket_watchdog_task: WebSocketWatchdogTask = None
        self.daily_price_collector_task: DailyPriceCollectorTask = None
        self.ohlcv_update_task: OhlcvUpdateTask = None
        self.premium_watchlist_generator_task: PremiumWatchlistGeneratorTask = None
        self.cache_warmup_task: CacheWarmupTask = None
        self.log_cleanup_task: LogCleanupTask = None
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

        # 실시간 스트리밍 전용 이벤트 로거 (logs/streaming/)
        self.streaming_event_logger = get_streaming_logger()
        
        web_api.set_ctx(self)

    def load_config_and_env(self):
        """설정 파일 로드 및 환경 초기화."""
        config_data = load_configs()
        self.full_config = config_data  # 전체 설정 저장

        # Pydantic 모델(AppConfig)을 dict로 변환
        config_dict = config_data
        if hasattr(config_data, "model_dump"):
            config_dict = config_data.model_dump()
        elif hasattr(config_data, "dict"):
            config_dict = config_data.dict()

        self.env = KoreaInvestApiEnv(config_dict, self.logger)
        self.market_clock = MarketClock(
            market_open_time=config_dict.get('market_open_time', "09:00"),
            market_close_time=config_dict.get('market_close_time', "15:40"),
            timezone=config_dict.get('market_timezone', "Asia/Seoul"),
            logger=self.logger
        )
        self.notification_service = NotificationService(self.market_clock)
        # ---------------------------------------------------------
        # [추가] Telegram Notifier 초기화 및 핸들러 등록
        telegram_token = config_dict.get("telegram_bot_token")
        telegram_chat_id = config_dict.get("telegram_chat_id")
        
        if telegram_token and telegram_chat_id:
            # WebAppContext 인스턴스 변수로 유지하여 생명주기 관리
            self.telegram_notifier = TelegramNotifier(
                bot_token=telegram_token, 
                chat_id=telegram_chat_id,
            )
            self.notification_service.register_external_handler(
                self.telegram_notifier.handle_event
            )
            self.logger.info("텔레그램 외부 알림 핸들러가 성공적으로 등록되었습니다.")
            
            # [추가] Telegram Reporter 초기화 (RankingTask 주입용)
            self.telegram_reporter = TelegramReporter(bot_token=telegram_token, chat_id=telegram_chat_id)
            self.logger.info("텔레그램 리포터가 초기화되었습니다.")
        else:
            self.logger.info("텔레그램 설정이 누락되어 알림 핸들러를 등록하지 않습니다.")
        # ---------------------------------------------------------
        self.logger.info("웹 앱: 환경 설정 로드 완료.")

        # [신규] MarketCalendarService 초기화
        self._mcs = MarketCalendarService(self.market_clock, self.logger, performance_profiler=self.pm)
        

    async def initialize_services(self, is_paper_trading: bool = True):
        """서비스 레이어 초기화. TradingApp._complete_api_initialization() 참조."""
        self.env.set_trading_mode(is_paper_trading)
        token_acquired = await self.env.get_access_token()
        if not token_acquired:
            self.logger.critical("웹 앱: 토큰 발급 실패.")
            return False
        # 모의투자 모드에서도 실전 토큰 사전 발급 (조회 API는 항상 실전 인증 사용)
        if is_paper_trading:
            await self.env.get_real_access_token()

        self.broker = BrokerAPIWrapper(
            env=self.env, logger=self.logger, market_clock=self.market_clock,
            market_calendar_service=self._mcs,
            streaming_logger=self.streaming_event_logger,
        )

        # [수정] MarketCalendarService에 Broker 주입 (Fetcher 로직은 Manager 내부로 이동)
        self._mcs.set_broker(self.broker)
        
        # [신규] 휴장일 정보 동기화 
        # 이를 통해 get_next_market_open_time 등이 임시공휴일을 정확히 인지하게 됨
        await self._mcs._sync_calendar_if_needed()

        # 캐시 매니저 생성
        # Pydantic 모델(AppConfig)을 dict로 변환하여 전달
        config_dict = self.full_config
        if hasattr(config_dict, "model_dump"):
            config_dict = config_dict.model_dump()
        elif hasattr(config_dict, "dict"):
            config_dict = config_dict.dict()

        perf_log = config_dict.get("performance_logging", False)
        perf_threshold = config_dict.get("performance_threshold", 0.1)
        # [변경] PerformanceProfiler 인스턴스 생성 및 주입 준비
        self.pm = PerformanceProfiler(enabled=perf_log, threshold=perf_threshold)

        cache_store = CacheStore(config_dict)
        cache_store.set_logger(self.logger)

        # Repository 초기화 (StockQueryService 주입을 위해 선 생성)
        self.stock_repository = StockRepository(logger=self.logger)

        self.market_data_service = MarketDataService(
            broker_api_wrapper=self.broker, env=self.env, logger=self.logger, market_clock=self.market_clock, cache_store=cache_store,
            market_calendar_service=self._mcs,
            performance_profiler=self.pm,
            stock_repository=self.stock_repository
        )

        # IndicatorService 초기화 (순환 참조 해결을 위해 먼저 생성 후 주입)
        self.indicator_service = IndicatorService(cache_store=cache_store, performance_profiler=self.pm)
        
        self.ranking_task = RankingTask(
            broker_api_wrapper=self.broker,
            stock_code_repository=self.stock_code_repository,
            env=self.env,
            logger=self.logger,
            market_clock=self.market_clock,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            telegram_reporter=getattr(self, 'telegram_reporter', None),
            market_calendar_service=self._mcs,
            market_data_service=self.market_data_service,
        )
        self.stock_query_service = StockQueryService(
            market_data_service=self.market_data_service, logger=self.logger, market_clock=self.market_clock,
            indicator_service=self.indicator_service,
            ranking_task=self.ranking_task,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            broker_api_wrapper=self.broker,
        )
        # IndicatorService에 StockQueryService 주입
        self.indicator_service.stock_query_service = self.stock_query_service
        # FavoriteService에 StockQueryService 주입 (현재가 조회용)
        self.favorite_service.stock_query_service = self.stock_query_service
        # StreamingService 초기화
        self.streaming_service = StreamingService(
            broker_api_wrapper=self.broker,
            logger=self.logger,
            market_clock=self.market_clock,
            market_data_service=self.market_data_service,
            streaming_logger=self.streaming_event_logger,
        )
        # PriceStreamService 초기화 — 체결가 틱 캐시 + StockRepository 업데이트 전담
        self.price_stream_service = PriceStreamService(
            stock_repo=self.stock_repository,
            logger=self.logger,
        )
        self.streaming_service.set_price_stream_service(self.price_stream_service)

        # StreamingStockRepo 초기화 (구독 상태 중앙 저장소)
        self.streaming_stock_repo = StreamingStockRepo(logger=self.logger)
        self.streaming_stock_repo.load_pt_desired_from_db("data/program_subscribe/program_trading.db")

        # PriceSubscriptionService 초기화 (StreamingService 생성 이후)
        self.price_subscription_service = PriceSubscriptionService(
            streaming_service=self.streaming_service,
            stock_repo=self.stock_repository,
            logger=self.logger,
            streaming_logger=self.streaming_event_logger,
            streaming_stock_repo=self.streaming_stock_repo,
            market_calendar=self._mcs,
        )

        # WebSocketWatchdogTask 초기화
        self.websocket_watchdog_task = WebSocketWatchdogTask(
            streaming_service=self.streaming_service,
            program_trading_stream_service=self.program_trading_stream_service,
            market_calendar_service=self._mcs,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            logger=self.logger,
            streaming_logger=self.streaming_event_logger,
            streaming_stock_repo=self.streaming_stock_repo,
            price_subscription_service=self.price_subscription_service,
        )

        self.daily_price_collector_task = DailyPriceCollectorTask(
            stock_query_service=self.stock_query_service,
            stock_code_repository=self.stock_code_repository,
            stock_repo=self.stock_repository,
            market_calendar_service=self._mcs,
            market_clock=self.market_clock,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            logger=self.logger,
        )

        self.ohlcv_update_task = OhlcvUpdateTask(
            stock_query_service=self.stock_query_service,
            stock_code_repository=self.stock_code_repository,
            stock_repo=self.stock_repository,
            market_calendar_service=self._mcs,
            market_clock=self.market_clock,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            logger=self.logger,
        )

        self.order_execution_service = OrderExecutionService(
            broker_api_wrapper=self.broker,
            logger=self.logger, market_clock=self.market_clock,
            performance_profiler=self.pm,
            notification_service=self.notification_service,
            market_calendar_service=self._mcs,
            price_subscription_service=self.price_subscription_service,
        )
        
        # [신규] 오닐 유니버스 서비스 초기화
        self.oneil_universe_service = OneilUniverseService(
            stock_query_service=self.stock_query_service,
            indicator_service=self.indicator_service,
            stock_code_repository=self.stock_code_repository,
            market_clock=self.market_clock,
            scraper_service=NaverFinanceScraperService(logger=self.logger),
            logger=self.logger,
            performance_profiler=self.pm,
            price_subscription_service=self.price_subscription_service,
        )

        self.premium_watchlist_generator_task = PremiumWatchlistGeneratorTask(
            universe_service=self.oneil_universe_service,
            market_calendar_service=self._mcs,
            market_clock=self.market_clock,
            notification_service=self.notification_service,
            logger=self.logger,
        )

        self.cache_warmup_task = CacheWarmupTask(
            market_data_service=self.market_data_service,
            stock_query_service=self.stock_query_service,
            universe_service=self.oneil_universe_service,
            market_calendar_service=self._mcs,
            market_clock=self.market_clock,
            notification_service=self.notification_service,
            logger=self.logger,
        )

        self.log_cleanup_task = LogCleanupTask(
            log_dir=self.logger.log_dir,
            days=30,
            market_calendar_service=self._mcs,
            market_clock=self.market_clock,
            logger=self.logger,
        )

        # NotificationQueueTask 초기화
        self.notification_queue_task = NotificationQueueTask(
            notification_service=self.notification_service,
            poll_interval=config_dict.get("notification_queue_poll_interval", 1.0),
            logger=self.logger,
        )

        # BackgroundScheduler 초기화 및 태스크 등록
        self.background_scheduler = BackgroundScheduler(
            logger=self.logger,
            performance_profiler=self.pm,
        )
        if self.ranking_task:
            self.background_scheduler.register(self.ranking_task)
        if self.websocket_watchdog_task:
            self.background_scheduler.register(self.websocket_watchdog_task)
        if self.daily_price_collector_task:
            self.background_scheduler.register(self.daily_price_collector_task)
        if self.ohlcv_update_task:
            self.background_scheduler.register(self.ohlcv_update_task)
        if self.premium_watchlist_generator_task:
            self.background_scheduler.register(self.premium_watchlist_generator_task)
        if self.cache_warmup_task:
            self.background_scheduler.register(self.cache_warmup_task)
        if self.log_cleanup_task:
            self.background_scheduler.register(self.log_cleanup_task)
        if self.notification_queue_task:
            self.background_scheduler.register(self.notification_queue_task)

        # ForegroundScheduler 초기화
        self.foreground_scheduler = ForegroundScheduler(
            background_scheduler=self.background_scheduler,
            logger=self.logger,
            performance_profiler=self.pm,
        )

        # 기동 시 포트폴리오/프리미엄 종목 구독 초기화
        asyncio.create_task(self._initialize_price_subscriptions())

        self.initialized = True
        mode = "모의투자" if is_paper_trading else "실전투자"
        self.logger.info(f"웹 앱: 서비스 초기화 완료 ({mode})")
        return True

    async def _initialize_price_subscriptions(self) -> None:
        """기동 시 포트폴리오(HIGH) 및 프리미엄 종목(MEDIUM) 구독을 초기화."""
        if not self.price_subscription_service:
            return

        from services.price_subscription_service import SubscriptionPriority

        # 1. 보유 종목 → HIGH 구독
        try:
            resp = await self.broker.get_account_balance()
            if resp and resp.rt_cd == "0" and resp.data:
                holdings = resp.data.get("output2", []) if isinstance(resp.data, dict) else []
                for item in holdings:
                    code = item.get("pdno", "").strip()
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
                codes = data.get("kospi", []) + data.get("kosdaq", [])
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

    def initialize_scheduler(self):
        """전략 스케줄러 생성 및 전략 등록 (자동 시작하지 않음, 웹 UI에서 수동 시작)."""
        self.scheduler = StrategyScheduler(
            virtual_trade_service=self.virtual_trade_service,
            order_execution_service=self.order_execution_service,
            stock_query_service=self.stock_query_service,
            stock_code_repository=self.stock_code_repository,
            market_clock=self.market_clock,
            market_calendar_service=self._mcs,
            logger=get_strategy_logger('StrategyScheduler'),
            dry_run=False,
            notification_service=self.notification_service,
            performance_profiler=self.pm,
            price_subscription_service=self.price_subscription_service,
        )

        # 거래량 돌파 전략 등록
        vb_strategy = VolumeBreakoutLiveStrategy(
            stock_query_service=self.stock_query_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('VolumeBreakoutLive'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=vb_strategy,
            interval_minutes=5,
            max_positions=3,
            order_qty=1,
            enabled=False,
            force_exit_on_close=True,  # 👈 단타 전략이므로 장 마감 전 강제 청산
            allow_pyramiding=False,    # 👈 단타 전략이므로 불타기 금지
        ))

        # 프로그램 매수 추종 전략 등록
        pbf_strategy = ProgramBuyFollowStrategy(
            stock_query_service=self.stock_query_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('ProgramBuyFollow'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=pbf_strategy,
            interval_minutes=10,
            max_positions=3,
            order_qty=1,
            enabled=False,
            force_exit_on_close=True,  # 👈 단타 전략이므로 장 마감 전 강제 청산
            allow_pyramiding=False,    # 👈 단타 전략이므로 불타기 금지
        ))

        # 전통적 거래량 돌파 전략 등록
        tvb_strategy = TraditionalVolumeBreakoutStrategy(
            stock_query_service=self.stock_query_service,
            stock_code_repository=self.stock_code_repository,
            market_clock=self.market_clock,
            logger=get_strategy_logger('TraditionalVolumeBreakout'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=tvb_strategy,
            interval_minutes=1,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=True,  # 👈 단타 전략이므로 장 마감 전 강제 청산
            allow_pyramiding=False,    # 👈 단타 전략이므로 불타기 금지
        ))

        # 오닐 스퀴즈 돌파 전략 등록
        osb_strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=self.stock_query_service,
            universe_service=self.oneil_universe_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('OneilSqueezeBreakout'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=osb_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 👈 오닐 전략은 오버나잇(홀딩) 허용!
            allow_pyramiding=True,     # 👈 오버나잇 전략이므로 불타기 허용
        ))
        
        self.osb_strategy = osb_strategy # (웹 API 하위 호환성 유지용)
        self.oneil_universe_service_ref = self.oneil_universe_service

        # 오닐 포켓 피봇 & BGU 전략 등록
        pp_strategy = OneilPocketPivotStrategy(
            stock_query_service=self.stock_query_service,
            universe_service=self.oneil_universe_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('OneilPocketPivot'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=pp_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 7주 홀딩 허용
            allow_pyramiding=True,      # 👈 오버나잇 전략이므로 불타기 허용
        ))

        # 하이 타이트 플래그 전략 등록
        htf_strategy = HighTightFlagStrategy(
            stock_query_service=self.stock_query_service,
            universe_service=self.oneil_universe_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('HighTightFlag'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=htf_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # HTF는 오버나잇 홀딩 허용
        ))

        # 첫 눌림목(Holy Grail) 전략 등록
        fp_strategy = FirstPullbackStrategy(
            stock_query_service=self.stock_query_service,
            universe_service=self.oneil_universe_service,
            market_clock=self.market_clock,
            logger=get_strategy_logger('FirstPullback'),
        )
        self.scheduler.register(StrategySchedulerConfig(
            strategy=fp_strategy,
            interval_minutes=3,
            max_positions=5,
            order_qty=1,
            enabled=False,
            force_exit_on_close=False,  # 스윙 전략: 오버나잇 허용
            allow_pyramiding=False,
        ))
        self.logger.info("웹 앱: 전략 스케줄러 초기화 완료 (수동 시작 대기)")

        # StrategyScheduler를 BackgroundScheduler에 어댑터로 등록
        if self.background_scheduler and self.scheduler:
            adapter = StrategySchedulerTaskAdapter(self.scheduler)
            self.background_scheduler.register(adapter)

    def start_background_tasks(self):
        """백그라운드 태스크 시작 — BackgroundScheduler에 위임."""
        # StreamingService에 콜백 등록 (내부 저장 → 재연결 시에도 자동 유지됨)
        if self.streaming_service:
            self.streaming_service._callback = self._web_realtime_callback

        if self.background_scheduler:
            asyncio.create_task(self.background_scheduler.start_all())

    async def shutdown(self):
        """서비스 종료 처리 — BackgroundScheduler에 위임."""
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

        if self.streaming_service:
            self.streaming_service.dispatch_realtime_message(data)

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
            sub_price_success = await self.streaming_service.subscribe_realtime_price(code)
            self.pm.log_timer(f"subscribe_realtime_price({code})", t_sub_price)

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
                    await self.streaming_service.unsubscribe_realtime_price(code)
                return False

        except Exception as e:
            self.logger.error(f"프로그램매매 구독 중 예외 발생 ({code}): {e}", exc_info=True)
            return False

    async def stop_program_trading(self, code: str):
        """특정 종목 프로그램매매 구독 해지."""
        if self.streaming_stock_repo and code in self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING):
            await self.streaming_service.unsubscribe_program_trading(code)
            await self.streaming_service.unsubscribe_realtime_price(code)
            await self.streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
            await self.streaming_stock_repo.mark_inactive(code, StreamingType.PROGRAM_TRADING)

    async def stop_all_program_trading(self):
        """모든 프로그램매매 구독 해지."""
        codes = sorted(self.streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)) if self.streaming_stock_repo else []
        for code in codes:
            await self.streaming_service.unsubscribe_program_trading(code)
            await self.streaming_service.unsubscribe_realtime_price(code)
            if self.streaming_stock_repo:
                await self.streaming_stock_repo.unmark_desired(code, StreamingType.PROGRAM_TRADING)
                await self.streaming_stock_repo.mark_inactive(code, StreamingType.PROGRAM_TRADING)