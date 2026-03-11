"""
웹 애플리케이션용 서비스 초기화 모듈.
TradingApp의 초기화 로직을 참고하여 서비스 레이어만 초기화한다.
"""
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
import json
import os
import time
from datetime import datetime, timedelta
import asyncio
from brokers.broker_api_wrapper import BrokerAPIWrapper
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from services.order_execution_service import OrderExecutionService
from managers.virtual_trade_manager import VirtualTradeManager
from market_data.stock_code_mapper import StockCodeMapper
from services.indicator_service import IndicatorService
from core.time_manager import TimeManager
from core.logger import Logger, get_strategy_logger
from core.performance_manager import PerformanceManager
from scheduler.strategy_scheduler import StrategyScheduler, StrategySchedulerConfig
from services.naver_finance_scraper import NaverFinanceScraper
from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy
from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.first_pullback_strategy import FirstPullbackStrategy
from services.oneil_universe_service import OneilUniverseService
from services.background_service import BackgroundService
from managers.realtime_data_manager import RealtimeDataManager
from managers.market_date_manager import MarketDateManager
from managers.notification_manager import NotificationManager
from managers.telegram_notifier import TelegramNotifier
from view.web import web_api  # 임포트 확인
from core.cache.cache_manager import CacheManager

class WebAppContext:
    """웹 앱에서 사용할 서비스 컨텍스트."""

    def __init__(self, app_context):
        self.logger = Logger()
        self.env = app_context.env if app_context else None
        self.full_config = {}  # [추가] 전체 설정을 담을 그릇
        self.time_manager: TimeManager = None
        self.broker: BrokerAPIWrapper = None
        self.trading_service: TradingService = None
        self.stock_query_service: StockQueryService = None
        self.order_execution_service: OrderExecutionService = None
        self.indicator_service: IndicatorService = None
        self.indicator_service: IndicatorService = None
        self.virtual_manager = VirtualTradeManager()
        self.virtual_manager.backfill_snapshots()  # 과거 CSV 기반 스냅샷 역산
        self.stock_code_mapper = StockCodeMapper(logger=self.logger)
        self.scheduler: StrategyScheduler = None
        self.oneil_universe_service: OneilUniverseService = None
        self.background_service: BackgroundService = None
        self.market_date_manager: MarketDateManager = None
        self.notification_manager: NotificationManager = None
        self.initialized = False
        self.pm: PerformanceManager = None

        # [변경] 실시간 데이터 관리자 도입
        self.realtime_data_manager = RealtimeDataManager(self.logger)
        
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
        self.time_manager = TimeManager(
            market_open_time=config_dict.get('market_open_time', "09:00"),
            market_close_time=config_dict.get('market_close_time', "15:30"),
            timezone=config_dict.get('market_timezone', "Asia/Seoul"),
            logger=self.logger
        )
        self.notification_manager = NotificationManager(self.time_manager)
        # ---------------------------------------------------------
        # [추가] Telegram Notifier 초기화 및 핸들러 등록
        telegram_token = config_dict.get("telegram_bot_token")
        telegram_chat_id = config_dict.get("telegram_chat_id")
        
        if telegram_token and telegram_chat_id:
            # WebAppContext 인스턴스 변수로 유지하여 생명주기 관리
            self.telegram_notifier = TelegramNotifier(
                bot_token=telegram_token, 
                chat_id=telegram_chat_id,
                allowed_categories=["TRADE"]
            )
            self.notification_manager.register_external_handler(
                self.telegram_notifier.handle_event
            )
            self.logger.info("텔레그램 외부 알림 핸들러가 성공적으로 등록되었습니다.")
        else:
            self.logger.info("텔레그램 설정이 누락되어 알림 핸들러를 등록하지 않습니다.")
        # ---------------------------------------------------------
        self.logger.info("웹 앱: 환경 설정 로드 완료.")

        # [신규] MarketDateManager 초기화
        self.market_date_manager = MarketDateManager(self.time_manager, self.logger)

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
            env=self.env, logger=self.logger, time_manager=self.time_manager,
            market_date_manager=self.market_date_manager
        )

        # [수정] MarketDateManager에 Broker 주입 (Fetcher 로직은 Manager 내부로 이동)
        self.market_date_manager.set_broker(self.broker)
        
        # 캐시 매니저 생성
        # Pydantic 모델(AppConfig)을 dict로 변환하여 전달
        config_dict = self.full_config
        if hasattr(config_dict, "model_dump"):
            config_dict = config_dict.model_dump()
        elif hasattr(config_dict, "dict"):
            config_dict = config_dict.dict()

        perf_log = config_dict.get("performance_logging", False)
        perf_threshold = config_dict.get("performance_threshold", 0.1)
        # [변경] PerformanceManager 인스턴스 생성 및 주입 준비
        self.pm = PerformanceManager(enabled=perf_log, threshold=perf_threshold)

        cache_manager = CacheManager(config_dict)
        cache_manager.set_logger(self.logger)


        self.trading_service = TradingService(
            self.broker, self.env, self.logger, self.time_manager, cache_manager=cache_manager,
            market_date_manager=self.market_date_manager,
            performance_manager=self.pm
        )

        # IndicatorService 초기화 (순환 참조 해결을 위해 먼저 생성 후 주입)
        self.indicator_service = IndicatorService(cache_manager=cache_manager, performance_manager=self.pm)
        self.background_service = BackgroundService(
            broker_api_wrapper=self.broker,
            stock_code_mapper=self.stock_code_mapper,
            env=self.env,
            logger=self.logger,
            time_manager=self.time_manager,
            trading_service=self.trading_service,
            performance_manager=self.pm,
            notification_manager=self.notification_manager,
        )
        self.stock_query_service = StockQueryService(
            self.trading_service, self.logger, self.time_manager,
            indicator_service=self.indicator_service,
            background_service=self.background_service,
            performance_manager=self.pm,
            notification_manager=self.notification_manager,
        )
        # IndicatorService에 StockQueryService 주입
        self.indicator_service.stock_query_service = self.stock_query_service

        self.order_execution_service = OrderExecutionService(
            self.trading_service, self.logger, self.time_manager,
            performance_manager=self.pm,
            notification_manager=self.notification_manager,
        )
        
        # [신규] 오닐 유니버스 서비스 초기화
        self.oneil_universe_service = OneilUniverseService(
            stock_query_service=self.stock_query_service,
            indicator_service=self.indicator_service,
            stock_code_mapper=self.stock_code_mapper,
            time_manager=self.time_manager,
            scraper_service=NaverFinanceScraper(logger=self.logger),
            logger=self.logger,
            performance_manager=self.pm
        )

        self.initialized = True
        mode = "모의투자" if is_paper_trading else "실전투자"
        self.logger.info(f"웹 앱: 서비스 초기화 완료 ({mode})")
        return True

    def get_env_type(self) -> str:
        if self.env is None:
            return "미설정"
        return "모의투자" if self.env.is_paper_trading else "실전투자"

    def is_market_open(self) -> bool:
        if self.time_manager is None:
            return False
        return self.time_manager.is_market_open()

    def get_current_time_str(self) -> str:
        if self.time_manager is None:
            return ""
        return self.time_manager.get_current_kst_time().strftime('%Y-%m-%d %H:%M:%S')

    # --- 전략 스케줄러 ---

    def initialize_scheduler(self):
        """전략 스케줄러 생성 및 전략 등록 (자동 시작하지 않음, 웹 UI에서 수동 시작)."""
        self.scheduler = StrategyScheduler(
            virtual_manager=self.virtual_manager,
            order_execution_service=self.order_execution_service,
            stock_query_service=self.stock_query_service,
            time_manager=self.time_manager,
            logger=get_strategy_logger('StrategyScheduler'),
            dry_run=False,
            notification_manager=self.notification_manager,
        )

        # 거래량 돌파 전략 등록
        vb_strategy = VolumeBreakoutLiveStrategy(
            stock_query_service=self.stock_query_service,
            time_manager=self.time_manager,
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
            time_manager=self.time_manager,
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
            stock_code_mapper=self.stock_code_mapper,
            time_manager=self.time_manager,
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
            time_manager=self.time_manager,
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
            time_manager=self.time_manager,
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
            time_manager=self.time_manager,
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
            time_manager=self.time_manager,
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

    def start_background_tasks(self):
        """백그라운드 태스크 시작."""
        # [변경] 매니저에게 위임
        self.realtime_data_manager.start_background_tasks()

        # 이전 구독 상태 자동 복원 (SQLite에 저장된 종목 재구독)
        saved_codes = self.realtime_data_manager.get_subscribed_codes()
        if saved_codes:
            asyncio.create_task(self._restore_program_trading(saved_codes))

        # 프로그램매매 연결 상태 워치독 시작
        asyncio.create_task(self._program_trading_watchdog())

        if self.background_service:
            # 투자자별 순매수 랭킹 백그라운드 갱신 (조회 API는 항상 실전 URL 사용하므로 모의투자에서도 동작)
            asyncio.create_task(self.background_service.refresh_investor_ranking())
            # 장마감 후 자동 갱신 스케줄러 (기본 랭킹 + 투자자 랭킹)
            asyncio.create_task(self.background_service.start_after_market_scheduler())

    async def shutdown(self):
        """서비스 종료 처리."""
        # [변경] 매니저에게 위임
        await self.realtime_data_manager.shutdown()
        self.logger.info("웹 앱: 서비스 종료 완료")

    # --- 프로그램매매 실시간 스트리밍 ---

    def _web_realtime_callback(self, data):
        """웹소켓 실시간 콜백: 기존 핸들러 + 웹 SSE 전달."""
        if self.stock_query_service:
            self.stock_query_service.dispatch_realtime_message(data)
        if data.get('type') == 'realtime_program_trading':
            item = data.get('data', {})
            # [추가] 현재가 정보 주입
            if self.stock_query_service:
                code = item.get('유가증권단축종목코드')
                price_data = self.stock_query_service.get_cached_realtime_price(code)
                if price_data:
                    if isinstance(price_data, dict):
                        item['price'] = price_data.get('price')
                        item['change'] = price_data.get('change')
                        item['rate'] = price_data.get('rate')
                        item['sign'] = price_data.get('sign')
                    else:
                        item['price'] = price_data

            # [변경] 매니저에게 데이터 처리 위임
            self.realtime_data_manager.on_data_received(item)

    async def _restore_program_trading(self, codes: list):
        """앱 시작 시 이전 구독 상태를 자동 복원 (백그라운드)."""
        self.logger.info(f"프로그램매매 구독 복원 시작: {codes}")
        success_count = 0
        failed_codes = []
        for code in codes:
            try:
                connected = await self.stock_query_service.connect_websocket(self._web_realtime_callback)
                if not connected:
                    self.logger.warning(f"프로그램매매 복원 실패 (WebSocket 연결 불가): {code}")
                    failed_codes.append(code)
                    continue
                await self.stock_query_service.subscribe_program_trading(code)
                await self.stock_query_service.subscribe_realtime_price(code)
                success_count += 1
            except Exception as e:
                self.logger.error(f"프로그램매매 복원 중 오류 ({code}): {e}")
                failed_codes.append(code)

        if failed_codes:
            self.logger.warning(f"복원에 실패한 구독 종목을 상태에서 제거합니다: {failed_codes}")
            for code in failed_codes:
                self.realtime_data_manager.remove_subscribed_code(code)

        self.logger.info(f"프로그램매매 구독 복원 완료: {success_count}/{len(codes)}개 종목")

    async def _program_trading_watchdog(self):
        """프로그램매매 WebSocket 연결 상태를 주기적으로 감시하고, 데이터 수신이 끊기면 재연결."""
        WATCHDOG_INTERVAL = 60   # 감시 주기 (초)
        DATA_GAP_THRESHOLD = 120  # 데이터 미수신 허용 최대 시간 (초)

        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                codes = self.realtime_data_manager.get_subscribed_codes()
                if not codes:
                    continue  # 구독 중인 종목 없으면 스킵

                if not self.time_manager or not self.time_manager.is_market_open():
                    continue  # 장 마감 시간이면 스킵

                # 조건 1: 수신 태스크가 죽었는지 확인
                receive_alive = (
                    self.trading_service
                    and self.trading_service.is_websocket_receive_alive()
                )

                # 조건 2: 데이터 수신 갭 확인
                last_ts = self.realtime_data_manager.last_data_ts
                data_gap = (time.time() - last_ts) if last_ts > 0 else float('inf')

                needs_reconnect = False
                if not receive_alive:
                    self.logger.warning(f"[워치독] WebSocket 수신 태스크가 종료됨. 재연결을 시도합니다.")
                    needs_reconnect = True
                elif data_gap > DATA_GAP_THRESHOLD:
                    self.logger.warning(f"[워치독] {data_gap:.0f}초간 데이터 미수신 (임계값: {DATA_GAP_THRESHOLD}초). 재연결을 시도합니다.")
                    needs_reconnect = True

                if needs_reconnect:
                    await self._force_reconnect_program_trading()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"[워치독] 오류 발생: {e}")

    async def _force_reconnect_program_trading(self):
        """WebSocket 연결을 강제로 끊고 재연결 + 재구독."""
        codes = self.realtime_data_manager.get_subscribed_codes()
        if not codes:
            return

        self.logger.info(f"[워치독] 강제 재연결 시작 (구독 종목: {codes})")
        try:
            # 1. 기존 WebSocket 연결 강제 종료
            await self.stock_query_service.trading_service.disconnect_websocket()
        except Exception as e:
            self.logger.warning(f"[워치독] 기존 연결 종료 중 오류 (무시): {e}")

        # 2. 새 연결 + 재구독
        success_count = 0
        failed_codes = []
        for code in codes:
            try:
                connected = await self.stock_query_service.connect_websocket(self._web_realtime_callback)
                if not connected:
                    self.logger.warning(f"[워치독] 재연결 실패: {code}")
                    failed_codes.append(code)
                    continue
                await self.stock_query_service.subscribe_program_trading(code)
                await self.stock_query_service.subscribe_realtime_price(code)
                success_count += 1
            except Exception as e:
                self.logger.error(f"[워치독] 재구독 중 오류 ({code}): {e}")
                failed_codes.append(code)

        if failed_codes:
            self.logger.warning(f"[워치독] 재구독에 실패한 종목을 상태에서 제거합니다: {failed_codes}")
            for code in failed_codes:
                self.realtime_data_manager.remove_subscribed_code(code)

        self.logger.info(f"[워치독] 강제 재연결 완료: {success_count}/{len(codes)}개 종목")

    async def start_program_trading(self, code: str) -> bool:
        """프로그램매매 구독 시작 (웹소켓 연결 + 구독). 이미 구독 중이면 스킵."""
        # [변경] 매니저를 통해 구독 상태 확인
        if self.realtime_data_manager.is_subscribed(code):
            # [추가] 구독 상태이지만 수신 태스크가 죽었으면 강제 재연결
            if (self.trading_service
                    and not self.trading_service.is_websocket_receive_alive()):
                self.logger.warning(f"[프로그램매매] {code} 구독 상태이나 수신 태스크 종료됨. 재연결 시도.")
                await self._force_reconnect_program_trading()
                
                # 재연결 과정에서 실패하여 구독 목록에서 제거되었는지 확인
                if not self.realtime_data_manager.is_subscribed(code):
                    self.logger.info(f"[프로그램매매] {code} 재연결 실패로 구독 해제됨. 신규 구독 재시도.")
                else:
                    return True
            else:
                return True

        try:
            connected = await self.stock_query_service.connect_websocket(self._web_realtime_callback)
            if not connected:
                self.logger.warning(f"프로그램매매 구독 실패 (WebSocket 연결 불가): {code}")
                return False

            sub_pt_success = await self.stock_query_service.subscribe_program_trading(code)
            sub_price_success = await self.stock_query_service.subscribe_realtime_price(code)

            if sub_pt_success and sub_price_success:
                self.realtime_data_manager.add_subscribed_code(code)
                self.logger.info(f"프로그램매매 신규 구독 성공: {code}")
                return True
            else:
                # 하나라도 실패하면, 성공했을 수 있는 다른 구독을 해지하여 상태를 정리한다.
                self.logger.warning(f"프로그램매매 구독 실패 (pt: {sub_pt_success}, price: {sub_price_success}) - {code}")
                if sub_pt_success:
                    await self.stock_query_service.unsubscribe_program_trading(code)
                if sub_price_success:
                    await self.stock_query_service.unsubscribe_realtime_price(code)
                return False

        except Exception as e:
            self.logger.error(f"프로그램매매 구독 중 예외 발생 ({code}): {e}", exc_info=True)
            return False

    async def stop_program_trading(self, code: str):
        """특정 종목 프로그램매매 구독 해지."""
        # [변경] 매니저를 통해 구독 상태 확인
        if self.realtime_data_manager.is_subscribed(code):
            await self.stock_query_service.unsubscribe_program_trading(code)
            await self.stock_query_service.unsubscribe_realtime_price(code) # [추가]
            self.realtime_data_manager.remove_subscribed_code(code)

    async def stop_all_program_trading(self):
        """모든 프로그램매매 구독 해지."""
        # [변경] 매니저에서 구독 목록 가져오기
        for code in self.realtime_data_manager.get_subscribed_codes():
            await self.stock_query_service.unsubscribe_program_trading(code)
            await self.stock_query_service.unsubscribe_realtime_price(code)
        self.realtime_data_manager.clear_subscribed_codes()