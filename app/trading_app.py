from app.app_context import AppInitializer, AppContext
from strategies.backtest_data_provider import BacktestDataProvider
from view.cli.cli_view import CLIView
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from core.time_manager import TimeManager
from core.logger import Logger
# 새로 분리된 핸들러 클래스 임포트
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
        self.time_manager = None
        self.logger = logger if logger else Logger()  # ✅ 주입 가능한 구조
        self.cli_view = None  # CLIView는 여기서 초기화됩니다.
        self.executor = None
        self.app_context: AppContext | None = None

        # 하위 호환성을 위해 유지. AppContext에서 채워집니다.
        self.order_execution_service = None
        self.stock_query_service = None
        self.backtest_data_provider = None
        self.broker = None

        self._load_configs_and_init_env()

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
            self.app_context = AppInitializer.initialize_services(
                env=self.env,
                logger=self.logger,
                time_manager=self.time_manager
            )
            # 하위 호환성을 위해 개별 서비스 속성도 채워줍니다.
            self.broker = self.app_context.broker
            self.order_execution_service = self.app_context.order_execution_service
            self.stock_query_service = self.app_context.stock_query_service
            self.backtest_data_provider = self.app_context.backtest_data_provider
            return True
        except Exception as e:
            error_message = f"API 클라이언트 초기화 실패: {e}"
            self.logger.critical(error_message, exc_info=True)
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

        # 서비스가 모두 초기화된 후에 Executor를 생성합니다.
        self.executor = UserActionExecutor(self)

        await self.select_environment()

        running = True
        while running:
            self.cli_view.display_current_time()
            self._display_menu()
            choice = await self.cli_view.get_user_input("메뉴를 선택하세요: ")
            running = await self.executor.execute(choice)
