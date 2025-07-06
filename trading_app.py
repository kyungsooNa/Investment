import asyncio
import json
import os
from datetime import datetime, timedelta

from strategies.backtest_data_provider import BacktestDataProvider
from app.cli_view import CLIView
from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from brokers.korea_investment.korea_invest_token_manager import TokenManager
from core.config_loader import load_config
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
import asyncio

# 새로 분리된 핸들러 클래스 임포트
from app.data_handlers import DataHandlers
from app.transaction_handlers import TransactionHandlers
from user_api.broker_api_wrapper import BrokerAPIWrapper

# config_loader.py에 이미 정의되어 있을 수 있으나, 독립 실행을 위해 여기에 포함
def load_config(file_path):
    """지정된 경로에서 YAML 또는 JSON 설정 파일을 로드합니다."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                import yaml
                return yaml.safe_load(f)
            except ImportError:
                f.seek(0)
                return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"설정 파일 형식이 올바르지 않습니다 ({file_path}): {e}")

class TradingApp:
    def __init__(self, main_config_path, tr_ids_config_path):
        self.main_config_path = main_config_path
        self.tr_ids_config_path = tr_ids_config_path

        self.env = None
        self.api_client = None
        self.trading_service = None
        self.time_manager = None
        self.logger = Logger()
        self.token_manager = None
        self.cli_view = None # CLIView는 여기서 초기화됩니다.

        self.data_handlers = None
        self.transaction_handlers = None
        self.backtest_data_provider = None

        self._load_configs_and_init_env()
        self.broker = None

    def _load_configs_and_init_env(self):
        """
        설정 파일을 로드하고 환경 및 TokenManager를 초기화합니다.
        """
        try:
            self.logger.info("애플리케이션 초기화 시작...")

            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.logger.debug(f"최종 config_data['tr_ids'] 내용 (init 전): {config_data.get('tr_ids')}")

            self.token_manager = TokenManager(
                token_file_path=config_data.get('token_file_path', 'config/token.json')
            )

            self.env = KoreaInvestApiEnv(config_data, self.token_manager, self.logger)

            self.time_manager = TimeManager(
                market_open_time=config_data.get('market_open_time', "09:00"),
                market_close_time=config_data.get('market_close_time', "15:30"),
                timezone=config_data.get('market_timezone', "Asia/Seoul"),
                logger=self.logger
            )
            self.cli_view = CLIView(self.time_manager, self.logger) # CLIView 인스턴스 초기화

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

            access_token = await self.env.get_access_token()
            if not access_token:
                raise Exception("API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

            self.api_client = KoreaInvestApiClient(self.env, token_manager=self.token_manager, logger=self.logger)
            self.trading_service = TradingService(self.api_client, self.env, self.logger, self.time_manager)

            self.data_handlers = DataHandlers(self.trading_service, self.logger, self.time_manager)
            # TransactionHandlers 초기화 시 cli_view 인자 제거 (이제 TransactionHandlers는 직접 입력을 받지 않습니다)
            self.transaction_handlers = TransactionHandlers(self.trading_service, self.logger, self.time_manager)
            self.broker = BrokerAPIWrapper(env=self.env, token_manager=self.token_manager, logger=self.logger)
            self.backtest_data_provider = BacktestDataProvider(
                broker=self.broker,
                time_manager=self.time_manager,
                logger=self.logger
            )

            self.logger.info(f"API 클라이언트 및 서비스 초기화 성공: {self.api_client}")
            return True

        except Exception as e:
            self.logger.critical(f"API 클라이언트 초기화 실패: {e}")
            self.cli_view.display_app_start_error(f"API 클라이언트 초기화 중 오류 발생: {e}")
            return False

    async def _select_environment(self):
        """애플리케이션 시작 시 모의/실전 투자 환경을 선택하고, 선택된 환경으로 API 클라이언트를 재초기화합니다."""
        selected = False
        while not selected:
            choice = await self.cli_view.select_environment_input()

            if choice == '1':
                self.env.set_trading_mode(False)
                self.logger.info("실전 투자 환경으로 설정되었습니다.")
                selected = True
            elif choice == '2':
                self.env.set_trading_mode(True)
                self.logger.info("모의 투자 환경으로 설정되었습니다.")
                selected = True
            else:
                self.cli_view.display_invalid_environment_choice()

        new_token_acquired = await self.env.get_access_token()

        if not new_token_acquired:
            self.logger.critical("선택된 환경의 토큰 발급에 실패했습니다. 애플리케이션을 종료합니다.")
            return False

        if not await self._complete_api_initialization():
            self.logger.critical("API 클라이언트 초기화 실패. 애플리케이션을 종료합니다.")
            return False

        return True

    def _display_menu(self):
        """사용자에게 메뉴 옵션을 출력하고 현재 상태를 표시합니다 (환경에 따라 동적)."""
        current_time = self.time_manager.get_current_kst_time()
        market_open_status = self.time_manager.is_market_open()
        market_status_str = "열려있음" if market_open_status else "닫혀있음"
        env_type = "모의투자" if self.env.is_paper_trading else "실전투자"

        self.cli_view.display_menu(
            env_type=env_type,
            current_time_str=current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
            market_status_str=market_status_str
        )

    async def _execute_action(self, choice):
        """사용자 선택에 따른 액션을 실행합니다."""
        running_status = True
        if choice == '0':
            self.logger.info("거래 환경 변경을 시작합니다.")
            if not await self._select_environment():
                running_status = False
        elif choice == '1':
            stock_code = await self.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
            await self.data_handlers.handle_get_current_stock_price(stock_code)
        elif choice == '2':
            balance = await self.trading_service.get_account_balance()
            if balance:
                self.cli_view.display_account_balance(balance)
            else:
                self.cli_view.display_account_balance_failure() # CLIView 메서드 사용
        elif choice == '3':
            # 사용자 입력을 CLIView에서 직접 받아서 TransactionHandlers로 전달
            stock_code = await self.cli_view.get_user_input("매수할 종목 코드를 입력하세요: ")
            qty_input = await self.cli_view.get_user_input("매수할 수량을 입력하세요: ")
            price_input = await self.cli_view.get_user_input("매수 가격을 입력하세요 (시장가: 0): ")
            await self.transaction_handlers.handle_buy_stock(stock_code, qty_input, price_input)
        elif choice == '4':
            # 사용자 입력을 CLIView에서 직접 받아서 TransactionHandlers로 전달
            stock_code = await self.cli_view.get_user_input("매도할 종목 코드를 입력하세요: ")
            qty_input = await self.cli_view.get_user_input("매도할 수량을 입력하세요: ")
            price_input = await self.cli_view.get_user_input("매도 가격을 입력하세요 (시장가: 0): ")
            await self.transaction_handlers.handle_sell_stock(stock_code, qty_input, price_input)
        elif choice == '5':
            stock_code = await self.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
            await self.data_handlers.handle_display_stock_change_rate(stock_code)
        elif choice == '6':
            stock_code = await self.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
            await self.data_handlers.handle_display_stock_vs_open_price(stock_code)
        elif choice == '7':
            if self.env.is_paper_trading:
                print("WARNING: 모의투자 환경에서는 시가총액 상위 종목 조회를 지원하지 않습니다.")
                self.logger.warning("모의투자 환경에서 시가총액 상위 종목 조회 시도 (미지원).")
            else:
                await self.data_handlers.handle_get_top_market_cap_stocks("0000")
        elif choice == '8':
            if self.env.is_paper_trading:
                print("WARNING: 모의투자 환경에서는 시가총액 1~10위 종목 조회를 지원하지 않습니다.")
                self.logger.warning("모의투자 환경에서 시가총액 1~10위 종목 조회 시도 (미지원).")
                running_status = True
            else:
                if await self.data_handlers.handle_get_top_10_market_cap_stocks_with_prices():
                    running_status = True  # 조회 성공 시에도 계속 실행
                else:
                    running_status = True  # 조회 실패 시에도 계속 실행
        elif choice == '9':
            await self.data_handlers.handle_upper_limit_stocks("0000", limit=500)
        elif choice == '10':
            if not self.time_manager.is_market_open():
                self.cli_view.display_warning_strategy_market_closed()
                self.logger.warning("시장 미개장 상태에서 전략 실행 시도")
                return running_status

            self.cli_view.display_strategy_running_message("모멘텀")

            from strategies.momentum_strategy import MomentumStrategy
            from strategies.strategy_executor import StrategyExecutor

            try:
                top_codes = await self.trading_service.get_top_market_cap_stocks_code("0000")

                if not isinstance(top_codes, dict) or top_codes.get('rt_cd') != '0':
                    self.cli_view.display_top_stocks_failure(top_codes.get('msg1', '알 수 없는 오류 또는 예상치 못한 응답 타입'))
                    self.logger.warning(f"시가총액 조회 실패. 응답: {top_codes}")
                    return running_status

                self.cli_view.display_top_stocks_success()

                top_stock_codes = [
                    item["mksc_shrn_iscd"]
                    for item in top_codes.get("output", [])[:30]
                    if "mksc_shrn_iscd" in item
                ]

                if not top_stock_codes:
                    self.cli_view.display_no_stocks_for_strategy()
                    return running_status

                strategy = MomentumStrategy(
                    broker=self.broker,
                    min_change_rate=10.0,
                    min_follow_through=3.0,
                    min_follow_through_time=10,
                    mode="live",
                    backtest_lookup=None,
                    logger=self.logger
                )
                executor = StrategyExecutor(strategy)
                result = await executor.execute(top_stock_codes)

                self.cli_view.display_strategy_results("모멘텀", result)
                self.cli_view.display_follow_through_stocks(result.get("follow_through", []))
                self.cli_view.display_not_follow_through_stocks(result.get("not_follow_through", []))

            except Exception as e:
                self.logger.error(f"모멘텀 전략 실행 중 오류 발생: {e}", exc_info=True)
                self.cli_view.display_strategy_error(f"전략 실행 실패: {e}")

        elif choice == '11':

            self.cli_view.display_strategy_running_message("모멘텀 백테스트")

            from strategies.momentum_strategy import MomentumStrategy

            from strategies.strategy_executor import StrategyExecutor

            try:

                count_input = await self.cli_view.get_user_input("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")

                try:

                    count = int(count_input) if count_input else 30

                    if count <= 0:
                        self.cli_view.display_invalid_input_warning("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.")

                        count = 30

                except ValueError:

                    self.cli_view.display_invalid_input_warning("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")

                    count = 30

                top_codes = await self.trading_service.get_top_market_cap_stocks_code("0000", count=count)

                if isinstance(top_codes, dict) and top_codes.get('rt_cd') != '0':

                    self.cli_view.display_top_stocks_failure(top_codes.get('msg1', '알 수 없는 오류 또는 예상치 못한 응답 타입'))

                    self.logger.warning(f"시가총액 조회 실패. 응답: {top_codes}")

                    return running_status

                if not top_codes:

                    self.cli_view.display_top_stocks_failure("결과 없음")

                    return running_status

                top_stock_codes = [

                    item["code"]

                    for item in top_codes[:count]

                    if "code" in item

                ]

                if not top_stock_codes:

                    self.cli_view.display_no_stocks_for_strategy()

                    return running_status

                strategy = MomentumStrategy(

                    broker=self.broker,

                    min_change_rate=10.0,

                    min_follow_through=3.0,

                    min_follow_through_time=10,

                    mode="backtest",

                    backtest_lookup=self.backtest_data_provider.realistic_price_lookup,

                    logger=self.logger

                )

                executor = StrategyExecutor(strategy)

                result = await executor.execute(top_stock_codes)

                self.cli_view.display_strategy_results("백테스트", result)

                self.cli_view.display_follow_through_stocks(result.get("follow_through", []))

                self.cli_view.display_not_follow_through_stocks(result.get("not_follow_through", []))

            except Exception as e:

                self.logger.error(f"[백테스트] 전략 실행 중 오류 발생: {e}")

                self.cli_view.display_strategy_error(f"전략 실행 실패: {e}")

        elif choice == '12':

            self.cli_view.display_strategy_running_message("GapUpPullback")

            from strategies.GapUpPullback_strategy import GapUpPullbackStrategy

            from strategies.strategy_executor import StrategyExecutor

            try:

                top_codes = await self.trading_service.get_top_market_cap_stocks_code("0000")

                if isinstance(top_codes, dict) and top_codes.get('rt_cd') == '0':

                    output_items = top_codes.get("output", [])

                    top_stock_codes = [

                        item["mksc_shrn_iscd"] for item in output_items if "mksc_shrn_iscd" in item

                    ]

                elif isinstance(top_codes, list):

                    top_stock_codes = [

                        item["code"] for item in top_codes if "code" in item

                    ]

                else:

                    self.cli_view.display_top_stocks_failure("응답 형식 오류")

                    return running_status

                if not top_stock_codes:

                    self.cli_view.display_no_stocks_for_strategy()

                    return running_status

                strategy = GapUpPullbackStrategy(

                    broker=self.broker,

                    min_gap_rate=5.0,

                    max_pullback_rate=2.0,

                    rebound_rate=2.0,

                    mode="live",

                    logger=self.logger

                )

                executor = StrategyExecutor(strategy)

                result = await executor.execute(top_stock_codes)

                self.cli_view.display_strategy_results("GapUpPullback", result)

                self.cli_view.display_gapup_pullback_selected_stocks(result.get("gapup_pullback_selected", []))

                self.cli_view.display_gapup_pullback_rejected_stocks(result.get("gapup_pullback_rejected", []))

            except Exception as e:

                self.logger.error(f"[GapUpPullback] 전략 실행 오류: {e}")

                self.cli_view.display_strategy_error(f"전략 실행 실패: {e}")
        elif choice == '13':
            stock_code = await self.cli_view.get_user_input("구독할 종목 코드를 입력하세요: ")
            await self.transaction_handlers.handle_realtime_price_quote_stream(stock_code)
        elif choice == '98':  # 14번 메뉴 추가: 토큰 무효화
            self.token_manager.invalidate_token()
            self.cli_view.display_token_invalidated_message()
        elif choice == '99':
            self.cli_view.display_exit_message()
            running_status = False
        else:
            self.cli_view.display_invalid_menu_choice()

        return running_status

    async def run_async(self):
        """비동기 애플리케이션을 실행합니다."""
        self.cli_view.display_welcome_message()

        if not await self._complete_api_initialization():
            return

        await self._select_environment()

        running = True
        while running:
            self.cli_view.display_current_time()
            self._display_menu()
            choice = await self.cli_view.get_user_input("메뉴를 선택하세요: ")
            running = await self._execute_action(choice)
