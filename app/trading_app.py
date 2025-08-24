from strategies.backtest_data_provider import BacktestDataProvider
from view.cli_view import CLIView
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
# 새로 분리된 핸들러 클래스 임포트
from services.stock_query_service import StockQueryService
from services.order_execution_service import OrderExecutionService
from brokers.broker_api_wrapper import BrokerAPIWrapper
from app.user_action_executor import UserActionExecutor

# common.types에서 모든 ResTypedDict와 ErrorCode 임포트
from typing import List
from common.types import (
    ResCommonResponse, ErrorCode,
    ResTopMarketCapApiItem
)  #


class TradingApp:
    def __init__(self, logger=None):
        self.env = None
        self.api_client = None
        # self.trading_service = None
        self.time_manager = None
        self.logger = logger if logger else Logger()  # ✅ 주입 가능한 구조
        self.cli_view = None  # CLIView는 여기서 초기화됩니다.

        self.order_execution_service = None
        self.stock_query_service = None
        self.backtest_data_provider = None

        self._load_configs_and_init_env()
        self.broker = None
        self.executor = UserActionExecutor(self)


    def _load_configs_and_init_env(self):
        """
        설정 파일을 로드하고 환경 및 TokenManager를 초기화합니다.
        """
        try:
            self.logger.info("애플리케이션 초기화 시작...")


            config_data = load_configs()

            self.logger.debug(f"최종 config_data['tr_ids'] 내용 (init 전): {config_data.get('tr_ids')}")

            self.env = KoreaInvestApiEnv(config_data, self.logger)

            self.time_manager = TimeManager(
                market_open_time=config_data.get('market_open_time', "09:00"),
                market_close_time=config_data.get('market_close_time', "15:30"),
                timezone=config_data.get('market_timezone', "Asia/Seoul"),
                logger=self.logger
            )
            self.cli_view = CLIView(self.env, self.time_manager, self.logger)  # CLIView 인스턴스 초기화

            self.logger.info("환경 설정 로드 및 KoreaInvestEnv 초기화 완료.")

        except FileNotFoundError as e:
            self.logger.error(f"설정 파일을 찾을 수 없습니다: {e}")
            raise
        except Exception as e:
            self.logger.critical(f"애플리케이션 초기화 실패: {e}")
            raise

    async def _complete_api_initialization(self):
        """API 클라이언트 및 서비스 객체 초기화를 수행합니다."""
        try:
            self.logger.info("API 클라이언트 초기화 시작 (선택된 환경 기반)...")

            self.broker = BrokerAPIWrapper(env=self.env, logger=self.logger, time_manager=self.time_manager)
            trading_service = TradingService(self.broker, self.env, self.logger, self.time_manager)

            self.order_execution_service = OrderExecutionService(trading_service, self.logger, self.time_manager)
            self.stock_query_service = StockQueryService(trading_service, self.logger, self.time_manager)

            # BacktestDataProvider에 BrokerAPIWrapper를 전달 (현재와 동일)
            self.backtest_data_provider = BacktestDataProvider(
                trading_service=trading_service,
                time_manager=self.time_manager,
                logger=self.logger
            )

            self.logger.info(f"API 클라이언트 및 서비스 초기화 성공.")  # self.api_client 출력 대신 일반 메시지
            return True
        except Exception as e:
            error_message = f"API 클라이언트 초기화 실패: {e}"
            self.logger.error(error_message)
            self.cli_view.display_app_start_error(error_message)
            return False

    async def select_environment(self):
        """애플리케이션 시작 시 모의/실전 투자 환경을 선택하고, 선택된 환경으로 API 클라이언트를 재초기화합니다."""
        selected = False
        while not selected:
            choice = await self.cli_view.select_environment_input()

            if choice == '1':
                self.env.set_trading_mode(True)
                self.logger.info("모의 투자 환경으로 설정되었습니다.")
                selected = True
            elif choice == '2':
                self.env.set_trading_mode(False)
                self.logger.info("실전 투자 환경으로 설정되었습니다.")
                selected = True
            else:
                self.cli_view.display_invalid_environment_choice(choice)

        new_token_acquired = await self.env.get_access_token()

        if not new_token_acquired:
            self.logger.critical("선택된 환경의 토큰 발급에 실패했습니다. 애플리케이션을 종료합니다.")
            return False

        return True

    def _display_menu(self):
        current_time = self.time_manager.get_current_kst_time()
        market_open_status = self.time_manager.is_market_open()
        market_status_str = "열려있음" if market_open_status else "닫혀있음"
        env_type = "모의투자" if self.env.is_paper_trading else "실전투자"

        # ✅ 실행기 단일 출처에서 메뉴 구성
        menu_items = self.executor.build_menu_items()

        self.cli_view.display_menu(
            env_type=env_type,
            current_time_str=current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
            market_status_str=market_status_str,
            menu_items=menu_items
        )

    async def run_async(self):
        """비동기 애플리케이션을 실행합니다."""
        self.cli_view.display_welcome_message()

        if not await self._complete_api_initialization():
            self.logger.critical("API 클라이언트 초기화 실패. 애플리케이션을 종료합니다.")
            return

        await self.select_environment()

        running = True
        while running:
            self.cli_view.display_current_time()
            self._display_menu()
            choice = await self.cli_view.get_user_input("메뉴를 선택하세요: ")
            running = await self.executor.execute(choice)
