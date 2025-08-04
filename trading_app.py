from strategies.backtest_data_provider import BacktestDataProvider
from view.cli_view import CLIView
from brokers.korea_investment.korea_invest_token_manager import TokenManager
from config.config_loader import load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
from strategies.momentum_strategy import MomentumStrategy
from strategies.strategy_executor import StrategyExecutor
from strategies.GapUpPullback_strategy import GapUpPullbackStrategy
# 새로 분리된 핸들러 클래스 임포트
from app.stock_query_service import StockQueryService
from app.order_execution_service import OrderExecutionService
from brokers.broker_api_wrapper import BrokerAPIWrapper

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
        self.trading_service = None
        self.time_manager = None
        self.logger = logger if logger else Logger()  # ✅ 주입 가능한 구조
        self.cli_view = None  # CLIView는 여기서 초기화됩니다.

        self.order_execution_service = None
        self.stock_query_service = None
        self.backtest_data_provider = None

        self._load_configs_and_init_env()
        self.broker = None

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

            # access_token = await self.env.get_access_token()
            # if not access_token:
            #     self.logger.critical("API 클라이언트 초기화 실패: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")
            #     raise Exception("API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

            # KoreaInvestApiClient는 이제 BrokerAPIWrapper 내부에서 관리됩니다.
            # 이 인스턴스를 직접 TradingService에 넘기지 않습니다.
            # self.api_client = KoreaInvestApiClient(self.env, token_manager=self.token_manager, logger=self.logger) # 이 줄은 삭제 또는 주석 처리

            # BrokerAPIWrapper를 한 번만 생성합니다.
            self.broker_wrapper = BrokerAPIWrapper(env=self.env, logger=self.logger, time_manager=self.time_manager)

            # TradingService에 BrokerAPIWrapper를 전달하도록 수정
            # TradingService의 __init__ 시그니처도 변경되어야 합니다 (broker_wrapper를 받도록)
            self.trading_service = TradingService(self.broker_wrapper, self.env, self.logger, self.time_manager)

            self.order_execution_service = OrderExecutionService(self.trading_service, self.logger, self.time_manager)
            self.stock_query_service = StockQueryService(self.trading_service, self.logger, self.time_manager)

            # BacktestDataProvider에 BrokerAPIWrapper를 전달 (현재와 동일)
            self.backtest_data_provider = BacktestDataProvider(
                broker_api_wrapper=self.broker_wrapper,  # self.broker 대신 self.broker_wrapper 사용 (일관성을 위해)
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

    async def _select_environment(self):
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
        """사용자에게 메뉴 옵션을 출력하고 현재 상태를 표시합니다 (환경에 따라 동적)."""
        current_time = self.time_manager.get_current_kst_time()
        market_open_status = self.time_manager.is_market_open()
        market_status_str = "열려있음" if market_open_status else "닫혀있음"
        env_type = "모의투자" if self.env.is_paper_trading else "실전투자"

        # CLIView가 메뉴 딕셔너리를 받아 출력하도록 수정하는 것을 권장합니다.
        # 아래는 예시이며, 실제 CLIView 구현에 맞춰 조정이 필요할 수 있습니다.
        menu_items = {
            "기본 기능": {
                "0": "거래 환경 변경",
                "1": "현재가 조회",
                "2": "계좌 잔고 조회",
                "3": "주식 매수",
                "4": "주식 매도",
            },
            "시세 조회": {
                "5": "전일대비 등락률 조회",
                "6": "시가대비 등락률 조회",
                "7": "실시간 호가 조회",
                "8": "시간대별 체결가 조회",
                # "9": "종목 뉴스 조회",
                "10": "ETF 정보 조회",
                # "11": "키워드로 종목 검색",
            },
            "랭킹/필터링": {
                # "12": "상위 랭킹 조회 (상승/하락/거래량 등)",
                "13": "시가총액 상위 조회 (실전 전용)",
                "14": "시가총액 상위 10개 현재가 조회 (실전 전용)",
                "15": "전일 상한가 종목 조회 (상위 500) (실전 전용)",
                "16": "전일 상한가 종목 조회 (상위) (실전 전용)",
                "17": "전일 상한가 종목 조회 (전체) (실전 전용)",
            },
            "실시간 구독": {
                "18": "실시간 체결가/호가 구독",
            },
            "전략 실행": {
                "20": "모멘텀 전략 실행",
                "21": "모멘텀 백테스트",
                "22": "GapUpPullback 전략 실행",
            },
            "기타": {
                "98": "토큰 무효화",
                "99": "종료",
            }
        }

        self.cli_view.display_menu(
            env_type=env_type,
            current_time_str=current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
            market_status_str=market_status_str,
            menu_items=menu_items  # 메뉴 데이터를 view로 전달
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
            await self.stock_query_service.handle_get_current_stock_price(stock_code)
        elif choice == '2':
            balance_response: ResCommonResponse = await self.trading_service.get_account_balance()

            if balance_response is None:
                self.cli_view.display_account_balance_failure("잔고 조회 실패: 응답 없음")
                self.logger.warning("계좌 잔고 조회 실패 - 응답이 None입니다.")
                return running_status

            if balance_response.rt_cd == ErrorCode.SUCCESS.value:
                self.cli_view.display_account_balance(balance_response.data)
                self.logger.info(f"계좌 잔고 조회 성공: {balance_response.data}")
            else:
                self.cli_view.display_account_balance_failure(balance_response.msg1)
                self.logger.warning(f"계좌 잔고 조회 실패: {balance_response.msg1}")

        elif choice == '3':
            # 사용자 입력을 CLIView에서 직접 받아서 TransactionHandlers로 전달
            stock_code = await self.cli_view.get_user_input("매수할 종목 코드를 입력하세요: ")
            qty_input = await self.cli_view.get_user_input("매수할 수량을 입력하세요: ")
            price_input = await self.cli_view.get_user_input("매수 가격을 입력하세요 (시장가: 0): ")
            await self.order_execution_service.handle_buy_stock(stock_code, qty_input, price_input)
        elif choice == '4':
            # 사용자 입력을 CLIView에서 직접 받아서 TransactionHandlers로 전달
            stock_code = await self.cli_view.get_user_input("매도할 종목 코드를 입력하세요: ")
            qty_input = await self.cli_view.get_user_input("매도할 수량을 입력하세요: ")
            price_input = await self.cli_view.get_user_input("매도 가격을 입력하세요 (시장가: 0): ")
            await self.order_execution_service.handle_sell_stock(stock_code, qty_input, price_input)
        elif choice == '5':
            stock_code = await self.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
            await self.stock_query_service.handle_display_stock_change_rate(stock_code)
        elif choice == '6':
            stock_code = await self.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
            await self.stock_query_service.handle_display_stock_vs_open_price(stock_code)
            # --- 새로 추가된 기능 ---
        elif choice == '7':
            stock_code = await self.cli_view.get_user_input("호가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
            await self.stock_query_service.handle_get_asking_price(stock_code)
        elif choice == '8':
            stock_code = await self.cli_view.get_user_input("시간대별 체결가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
            await self.stock_query_service.handle_get_time_concluded_prices(stock_code)
        elif choice == '9':
            stock_code = await self.cli_view.get_user_input("뉴스를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
            await self.stock_query_service.handle_get_stock_news(stock_code)
        elif choice == '10':
            etf_code = await self.cli_view.get_user_input("정보를 조회할 ETF 코드를 입력하세요(나스닥 ETF: 133690): ")
            await self.stock_query_service.handle_get_etf_info(etf_code)
        elif choice == '11':
            keyword = await self.cli_view.get_user_input("검색할 키워드를 입력하세요: ")
            await self.stock_query_service.handle_search_stocks_by_keyword(keyword)
        elif choice == '12':
            category = await self.cli_view.get_user_input("조회할 랭킹 종류를 입력하세요 (rise|fall|volume|foreign): ")
            await self.stock_query_service.handle_get_top_stocks(category.lower())
        # --- 여기까지 ---

        elif choice == '13':  # 기존 7번
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("시가총액 상위 종목 조회")
            else:
                await self.stock_query_service.handle_get_top_market_cap_stocks("0000")
        elif choice == '14':  # 기존 8번
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("시가총액 1~10위 종목 조회")
            else:
                await self.stock_query_service.handle_get_top_10_market_cap_stocks_with_prices()
        elif choice == '15':  # 기존 9번
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (상위 500)")
            else:
                await self.stock_query_service.handle_upper_limit_stocks("0000", limit=500)
        elif choice == '16':  # 기존 14번
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (상위)")
            else:
                await self.stock_query_service.handle_yesterday_upper_limit_stocks()
        elif choice == '17':
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (전체)")
            else:
                await self.stock_query_service.handle_current_upper_limit_stocks()

        elif choice == '18':
            stock_code = await self.cli_view.get_user_input("구독할 종목 코드를 입력하세요: ")
            field_input = await self.cli_view.get_user_input("구독할 데이터 타입을 입력하세요 (price, quote 중 택1 또는 쉼표로 구분): ")
            stock_codes = [stock_code]
            fields = [field.strip() for field in field_input.split(",") if field.strip() in {"price", "quote"}]

            if not fields:
                self.cli_view.display_strategy_error("올바른 필드를 입력하세요. (price, quote 중 선택)")
                return

            await self.stock_query_service.handle_realtime_stream(stock_codes, fields, duration=30)

        elif choice == '20':
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("모멘텀")
            if not self.time_manager.is_market_open():
                self.cli_view.display_warning_strategy_market_closed()
                self.logger.warning("시장 미개장 상태에서 전략 실행 시도")
                return running_status

            self.cli_view.display_strategy_running_message("모멘텀")

            try:
                # trading_service.get_top_market_cap_stocks_code는 이제 ResCommonResponse를 반환
                top_codes_response: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
                    "0000")

                if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
                    self.cli_view.display_top_stocks_failure(top_codes_response.msg1 or "알 수 없는 오류")
                    self.logger.warning(f"시가총액 조회 실패. 응답: {top_codes_response}")
                    return running_status

                # 'data' 필드에서 실제 목록을 가져옴
                top_codes_list: List[ResTopMarketCapApiItem] = getattr(top_codes_response, 'data', [])
                if not top_codes_list:  # 데이터가 비어있는 경우
                    self.cli_view.display_top_stocks_failure("시가총액 상위 종목 데이터 없음.")
                    self.logger.warning("시가총액 상위 종목 데이터 없음.")
                    return running_status

                self.cli_view.display_top_stocks_success()

                top_stock_codes = [
                    item.mksc_shrn_iscd
                    for item in top_codes_list[:30]  # data 필드에 List[ResTopMarketCapApiItem]이 있으므로 그대로 사용
                    if item.mksc_shrn_iscd
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

        elif choice == '21':
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("모멘텀")
            self.cli_view.display_strategy_running_message("모멘텀 백테스트")

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

                # trading_service.get_top_market_cap_stocks_code는 이제 ResCommonResponse를 반환
                top_codes_response: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
                    "0000", count=count)

                if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
                    self.cli_view.display_top_stocks_failure(top_codes_response.msg1 or "알 수 없는 오류")
                    self.logger.warning(f"시가총액 조회 실패. 응답: {top_codes_response}")
                    return running_status

                # 'data' 필드에서 실제 목록을 가져옴
                top_codes_list: List[ResTopMarketCapApiItem] = getattr(top_codes_response, "data", [])
                if not top_codes_list:  # 데이터가 비어있는 경우
                    self.cli_view.display_top_stocks_failure("시가총액 상위 종목 데이터 없음 (백테스트).")
                    self.logger.warning("시가총액 상위 종목 데이터 없음 (백테스트).")
                    return running_status

                top_stock_codes = [
                    item.get('mksc_shrn_iscd')  # 백테스트에서는 'code' 대신 'mksc_shrn_iscd' 사용해야 할 수 있음. API 스키마 확인 필요.
                    for item in top_codes_list[:count]
                    if isinstance(item, dict) and "mksc_shrn_iscd" in item
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

        elif choice == '22':
            if self.env.is_paper_trading:
                self.cli_view.display_warning_paper_trading_not_supported("모멘텀")
            self.cli_view.display_strategy_running_message("GapUpPullback")

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

                # trading_service.get_top_market_cap_stocks_code는 이제 ResCommonResponse를 반환
                top_codes_response: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
                    "0000", count=count)

                if top_codes_response.rt_cd == ErrorCode.SUCCESS.value:  # Enum 값 사용
                    top_codes_list: List[ResTopMarketCapApiItem] = getattr(top_codes_response,'data', [])

                    if not top_codes_list:  # 데이터가 비어있는 경우
                        self.cli_view.display_no_stocks_for_strategy("시가총액 상위 종목 데이터 없음 (백테스트).")
                        self.logger.warning("시가총액 상위 종목 데이터 없음 (백테스트).")
                        return running_status

                    top_stock_codes = [
                        item.get('mksc_shrn_iscd')  # 백테스트에서는 'code' 대신 'mksc_shrn_iscd' 사용해야 할 수 있음. API 스키마 확인 필요.
                        for item in top_codes_list[:count]
                        if isinstance(item, dict) and "mksc_shrn_iscd" in item
                    ]
                else:  # top_codes_response["rt_cd"] != ErrorCode.SUCCESS.value
                    self.cli_view.display_top_stocks_failure(getattr(top_codes_response.msg1, '응답 형식 오류'))
                    self.logger.warning(f"GapUpPullback 시가총액 조회 실패: {top_codes_response}")
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

        elif choice == '98':  # 14번 메뉴 추가: 토큰 무효화
            self.env.invalidate_token()
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
            self.logger.critical("API 클라이언트 초기화 실패. 애플리케이션을 종료합니다.")
            return

        await self._select_environment()

        running = True
        while running:
            self.cli_view.display_current_time()
            self._display_menu()
            choice = await self.cli_view.get_user_input("메뉴를 선택하세요: ")
            running = await self._execute_action(choice)
