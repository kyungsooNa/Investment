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


class WebAppContext:
    """웹 앱에서 사용할 서비스 컨텍스트."""

    def __init__(self):
        self.logger = Logger()
        self.env: KoreaInvestApiEnv = None
        self.time_manager: TimeManager = None
        self.broker: BrokerAPIWrapper = None
        self.trading_service: TradingService = None
        self.stock_query_service: StockQueryService = None
        self.order_execution_service: OrderExecutionService = None
        self.initialized = False

    def load_config_and_env(self):
        """설정 파일 로드 및 환경 초기화."""
        config_data = load_configs()
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
