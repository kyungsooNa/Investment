from __future__ import annotations
from typing import TYPE_CHECKING

# 순환 참조 방지를 위한 타입 체킹 블록
if TYPE_CHECKING:
    from brokers.broker_api_wrapper import BrokerAPIWrapper
    from services.trading_service import TradingService
    from services.order_execution_service import OrderExecutionService
    from services.stock_query_service import StockQueryService
    from strategies.backtest_data_provider import BacktestDataProvider
    from core.time_manager import TimeManager
    from core.logger import Logger
    from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv

from brokers.broker_api_wrapper import BrokerAPIWrapper
from services.trading_service import TradingService
from services.order_execution_service import OrderExecutionService
from services.stock_query_service import StockQueryService
from strategies.backtest_data_provider import BacktestDataProvider


class AppContext:
    """애플리케이션의 핵심 서비스들을 담는 컨테이너 클래스"""
    def __init__(self,
                 broker: BrokerAPIWrapper,
                 trading_service: TradingService,
                 order_execution_service: OrderExecutionService,
                 stock_query_service: StockQueryService,
                 backtest_data_provider: BacktestDataProvider):
        self.broker = broker
        self.trading_service = trading_service
        self.order_execution_service = order_execution_service
        self.stock_query_service = stock_query_service
        self.backtest_data_provider = backtest_data_provider


class AppInitializer:
    """서비스 객체들을 생성하고 의존성을 주입하는 팩토리 클래스"""
    @staticmethod
    def initialize_services(env: KoreaInvestApiEnv, logger: Logger, time_manager: TimeManager) -> AppContext:
        """API 클라이언트 및 서비스 객체 초기화를 수행하고 컨텍스트 객체를 반환합니다."""
        logger.info("API 클라이언트 및 서비스 초기화 시작...")
        broker = BrokerAPIWrapper(env=env, logger=logger, time_manager=time_manager)
        trading_service = TradingService(broker, env, logger, time_manager)
        order_execution_service = OrderExecutionService(trading_service, logger, time_manager)
        stock_query_service = StockQueryService(trading_service, logger, time_manager)
        backtest_data_provider = BacktestDataProvider(
            trading_service=trading_service, time_manager=time_manager, logger=logger
        )
        logger.info("API 클라이언트 및 서비스 초기화 성공.")
        return AppContext(
            broker=broker, trading_service=trading_service,
            order_execution_service=order_execution_service, stock_query_service=stock_query_service,
            backtest_data_provider=backtest_data_provider
        )