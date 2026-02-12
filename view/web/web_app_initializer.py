"""
웹 애플리케이션용 서비스 초기화 모듈.
TradingApp의 초기화 로직을 참고하여 서비스 레이어만 초기화한다.
"""
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.broker_api_wrapper import BrokerAPIWrapper
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from services.order_execution_service import OrderExecutionService
from core.time_manager import TimeManager
from core.logger import Logger
from view.web import web_api  # 임포트 확인

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
        self.initialized = False
        # 프로그램매매 실시간 스트리밍용
        self._pt_queues: list = []
        self._pt_code: str | None = None
        web_api.set_ctx(self)

    def load_config_and_env(self):
        """설정 파일 로드 및 환경 초기화."""
        config_data = load_configs()
        self.full_config = config_data  # 전체 설정 저장
        self.env = KoreaInvestApiEnv(config_data, self.logger)
        self.time_manager = TimeManager(
            market_open_time=config_data.get('market_open_time', "09:00"),
            market_close_time=config_data.get('market_close_time', "15:30"),
            timezone=config_data.get('market_timezone', "Asia/Seoul"),
            logger=self.logger
        )
        self.logger.info("웹 앱: 환경 설정 로드 완료.")

    async def initialize_services(self, is_paper_trading: bool = True):
        """서비스 레이어 초기화. TradingApp._complete_api_initialization() 참조."""
        self.env.set_trading_mode(is_paper_trading)
        token_acquired = await self.env.get_access_token()
        if not token_acquired:
            self.logger.critical("웹 앱: 토큰 발급 실패.")
            return False

        self.broker = BrokerAPIWrapper(
            env=self.env, logger=self.logger, time_manager=self.time_manager
        )
        self.trading_service = TradingService(
            self.broker, self.env, self.logger, self.time_manager
        )
        self.stock_query_service = StockQueryService(
            self.trading_service, self.logger, self.time_manager
        )
        self.order_execution_service = OrderExecutionService(
            self.trading_service, self.logger, self.time_manager
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

    # --- 프로그램매매 실시간 스트리밍 ---

    def _web_realtime_callback(self, data):
        """웹소켓 실시간 콜백: 기존 핸들러 + 웹 SSE 전달."""
        if self.trading_service:
            self.trading_service._default_realtime_message_handler(data)
        if data.get('type') == 'realtime_program_trading':
            item = data.get('data', {})
            for q in list(self._pt_queues):
                try:
                    q.put_nowait(item)
                except Exception:
                    pass

    async def start_program_trading(self, code: str) -> bool:
        """프로그램매매 구독 시작 (웹소켓 연결 + 구독)."""
        connected = await self.broker.connect_websocket(self._web_realtime_callback)
        if not connected:
            return False
        await self.trading_service.subscribe_program_trading(code)
        self._pt_code = code
        return True

    async def stop_program_trading(self):
        """프로그램매매 구독 해지."""
        if self._pt_code:
            await self.trading_service.unsubscribe_program_trading(self._pt_code)
            self._pt_code = None
