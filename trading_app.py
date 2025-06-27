# trading_app.py
from brokers.korea_investment.korea_invest_client import KoreaInvestApiClient
from core.config_loader import load_config
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
import asyncio  # 비동기 sleep을 위해 필요

# 새로 분리된 핸들러 클래스 임포트
from app.data_handlers import DataHandlers
from app.transaction_handlers import TransactionHandlers
from user_api.broker_api_wrapper import BrokerAPIWrapper  # wrapper import 추가


class TradingApp:
    """
    한국투자증권 Open API 애플리케이션의 사용자 인터페이스 (CLI)를 관리하는 클래스.
    모든 비즈니스 로직은 TradingService를 통해 처리됩니다.
    """

    def __init__(self, main_config_path, tr_ids_config_path):
        self.main_config_path = main_config_path
        self.tr_ids_config_path = tr_ids_config_path

        self.env = None
        self.api_client = None
        self.trading_service = None
        self.time_manager = None
        self.logger = Logger()

        # 핸들러 클래스 인스턴스 (초기화 시에는 None)
        self.data_handlers = None
        self.transaction_handlers = None

        # 초기 설정 로드 및 환경 클래스만 초기화 (API 클라이언트는 환경 선택 후 초기화)
        self._load_configs_and_init_env()
        self.broker = None

    def _load_configs_and_init_env(self):
        """환경 설정 파일 로드 및 KoreaInvestEnv 초기화."""
        try:
            self.logger.info("애플리케이션 초기화 시작...")

            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.env = KoreaInvestApiEnv(config_data, self.logger)

            # TimeManager 초기화 (환경 설정 로드 후)
            self.time_manager = TimeManager(
                market_open_time=config_data.get('market_open_time', "09:00"),
                market_close_time=config_data.get('market_close_time', "15:30"),
                timezone=config_data.get('market_timezone', "Asia/Seoul"),
                logger=self.logger
            )
            self.logger.info("환경 설정 로드 및 KoreaInvestEnv 초기화 완료.")

        except FileNotFoundError as e:
            self.logger.error(f"설정 파일을 찾을 수 없습니다: {e}")
            raise
        except Exception as e:
            self.logger.critical(f"애플리케이션 초기화 실패: {e}")
            raise

    async def _complete_api_initialization(self):
        """API 클라이언트 및 서비스 계층 초기화를 수행합니다."""
        try:
            self.logger.info("API 클라이언트 초기화 시작 (선택된 환경 기반)...")

            # 접근 토큰 발급
            access_token = self.env.get_access_token()
            if not access_token:
                raise Exception("API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

            # --- API 클라이언트 및 서비스 계층 인스턴스 재초기화 ---
            self.api_client = KoreaInvestApiClient(self.env, self.logger)
            self.trading_service = TradingService(self.api_client, self.env, self.logger, self.time_manager)

            # 핸들러 클래스 인스턴스화 (서비스와 로거, 타임 매니저 주입)
            self.data_handlers = DataHandlers(self.trading_service, self.logger, self.time_manager)
            self.transaction_handlers = TransactionHandlers(self.trading_service, self.logger, self.time_manager)
            # -----------------------------------------------------
            self.broker = BrokerAPIWrapper(env=self.env, logger=self.logger)

            self.logger.info(f"API 클라이언트 및 서비스 초기화 성공: {self.api_client}")
            return True

        except Exception as e:
            self.logger.critical(f"API 클라이언트 초기화 실패: {e}")
            print(f"ERROR: API 클라이언트 초기화 중 오류 발생: {e}")
            return False

    async def _select_environment(self):
        """애플리케이션 시작 시 모의/실전 투자 환경을 선택하고, 선택된 환경으로 API 클라이언트를 초기화합니다."""
        selected = False
        while not selected:
            print("\n--- 거래 환경 선택 ---")
            print("1. 모의투자")
            print("2. 실전투자")
            print("-----------------------")
            choice = (await asyncio.to_thread(input, "환경을 선택하세요 (숫자 입력): ")).strip()

            if choice == '1':
                self.env.set_trading_mode(True)  # 모의투자 설정
                selected = True
            elif choice == '2':
                self.env.set_trading_mode(False)  # 실전투자 설정
                selected = True
            else:
                print("유효하지 않은 선택입니다. '1' 또는 '2'를 입력해주세요.")

        # --- 환경 선택 후 토큰 강제 재발급 및 API 클라이언트 재초기화 ---
        # get_access_token은 이미 동기 함수이므로 await 제거
        new_token_acquired = self.env.get_access_token(force_new=True)  # <--- await 제거

        # 토큰이 성공적으로 발급되었는지 확인 (None이 아니면 성공)
        if not new_token_acquired:  # new_token_acquired는 이제 str 또는 None
            self.logger.critical("선택된 환경의 토큰 발급에 실패했습니다. 애플리케이션을 종료합니다.")
            return False  # 토큰 발급 실패 시 앱 종료 유도

        # 토큰 발급 성공 시 _complete_api_initialization 호출 (await으로)
        if not await self._complete_api_initialization():
            self.logger.critical("API 클라이언트 초기화 실패. 애플리케이션을 종료합니다.")
            return False
        return True

    def _display_menu(self):
        """사용자에게 메뉴 옵션을 출력하고 현재 시간을 포함합니다 (환경에 따라 동적)."""
        current_time = self.time_manager.get_current_kst_time()
        market_open_status = self.time_manager.is_market_open()
        market_status_str = "열려있음" if market_open_status else "닫혀있음"

        env_type = "모의투자" if self.env.is_paper_trading else "실전투자"

        print(
            f"\n--- 한국투자증권 API 애플리케이션 (환경: {env_type}, 현재: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 시장: {market_status_str}) ---")
        # --- 메뉴 순서 변경 ---
        print("1. 주식 현재가 조회 (삼성전자)")
        print("2. 계좌 잔고 조회")
        print("3. 주식 매수 주문 (삼성전자 1주, 지정가)")
        print("4. 실시간 주식 체결가/호가 구독 (삼성전자)")
        print("5. 주식 전일대비 등락률 조회 (삼성전자)")
        print("6. 주식 시가대비 조회 (삼성전자)")
        print("7. 시가총액 상위 종목 조회 (실전전용)")
        print("8. 시가총액 1~10위 종목 현재가 조회 (실전전용)")
        print("9. 상한가 종목 조회 (상위 500개 종목 기준)")
        print("10. 모멘텀 전략 실행 (상승 추세 필터링)")
        print("11. 모멘텀 전략 백테스트 실행")

        print("0. 종료")
        print("-----------------------------------")

    async def _execute_action(self, choice):
        """사용자 선택에 따라 해당 작업을 실행하고 결과를 콘솔에 출력합니다."""
        running_status = True

        if choice == '1':
            await self.data_handlers.handle_get_current_stock_price("005930")
        elif choice == '2':
            await self.data_handlers.handle_get_account_balance()
        elif choice == '3':
            await self.transaction_handlers.handle_place_buy_order("005930", "58500", "1", "00")
        elif choice == '4':
            await self.transaction_handlers.handle_realtime_price_quote_stream("005930")
        elif choice == '5':
            await self.data_handlers.handle_display_stock_change_rate("005930")
        elif choice == '6':
            await self.data_handlers.handle_display_stock_vs_open_price("005930")
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
                    running_status = False
        elif choice == '9':
            await self.data_handlers.handle_upper_limit_stocks("0000", limit=500)
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            running_status = False
        elif choice == '10':
            # 시장이 열려있는 경우만 전략 실행
            if not self.time_manager.is_market_open():
                print("시장 개장 시간에만 전략 실행이 가능합니다.")
                self.logger.warning("시장 미개장 상태에서 전략 실행 시도")
                return running_status

            print("\n모멘텀 전략 실행 중...")

            from services.momentum_strategy import MomentumStrategy
            from services.strategy_executor import StrategyExecutor

            try:
                # 1~30위 시가총액 종목 가져오기
                top_response = await self.trading_service.get_top_market_cap_stocks("0000")

                # 1. 응답이 딕셔너리이고, 'rt_cd'가 '0'이 아닌 경우 (서비스 레벨 오류)
                #   예: 모의투자 미지원 오류
                if isinstance(top_response, dict) and top_response.get('rt_cd') != '0':
                    print("시가총액 상위 종목 조회 실패:", top_response.get('msg1', '알 수 없는 오류'))
                    return running_status  # 오류 상태를 반환하거나 적절히 처리

                # 2. 응답이 리스트이지만 비어있는 경우 (API 클라이언트 레벨 오류 또는 데이터 없음)
                #   예: API 호출 실패, 응답 데이터 파싱 실패 등
                elif isinstance(top_response, list) and not top_response:
                    print("시가총액 상위 종목 조회 실패: 데이터를 가져오지 못했습니다. (API 응답 오류 또는 데이터 없음)")
                    return running_status  # 오류 상태를 반환하거나 적절히 처리

                # 3. 응답이 비어있지 않은 리스트인 경우 (성공)
                #   이제 top_response는 실제 시가총액 상위 종목 목록입니다.
                elif isinstance(top_response, list) and top_response:
                    print("시가총액 상위 종목 조회 성공!")
                    # 성공적으로 가져온 종목 데이터를 여기서 활용합니다.
                    # 예시:
                    # for stock in top_response:
                    #     print(f"종목 코드: {stock['code']}, 종목명: {stock['name']}, 시가총액: {stock['market_cap']}")
                    pass  # 여기에 비즈니스 로직(예: 다음 단계 실행)을 추가합니다.

                # 4. 예상치 못한 응답 타입 (혹시 모를 상황 대비)
                else:
                    print(f"시가총액 상위 종목 조회 응답이 예상과 다릅니다: {type(top_response)} - {top_response}")
                    return running_status  # 오류 상태를 반환하거나 적절히 처리

                top_stock_codes = [
                    item["mksc_shrn_iscd"]
                    for item in top_response["output"][:30]
                    if "mksc_shrn_iscd" in item
                ]

                # 전략 실행기 구성
                strategy = MomentumStrategy(
                    broker=self.broker,
                    min_change_rate=10.0,
                    min_follow_through=3.0,
                    min_follow_through_time=10,  # 10분 후 상승률 기준으로 판단
                    mode="live",
                    backtest_lookup=None,
                    logger=self.logger
                )
                executor = StrategyExecutor(strategy)
                result = await executor.execute(top_stock_codes)

                print("\n📈 [모멘텀 전략 결과]")
                print("📌 Follow Through 종목:")
                for s in result["follow_through"]:
                    print(f" - {s}")

                print("📌 Follow 실패 종목:")
                for s in result["not_follow_through"]:
                    print(f" - {s}")

            except Exception as e:
                self.logger.error(f"모멘텀 전략 실행 중 오류 발생: {e}")
                print(f"[오류] 전략 실행 실패: {e}")

        elif choice == '11':
            print("\n[모멘텀 전략 백테스트 실행 중...]")

            from services.momentum_strategy import MomentumStrategy
            from services.strategy_executor import StrategyExecutor

            try:
                # 사용자에게 입력받기
                count_input = input("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ").strip()

                try:
                    count = int(count_input) if count_input else 30
                    if count <= 0:
                        print("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.")
                        count = 30
                except ValueError:
                    print("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")
                    count = 30

                top_response = await self.trading_service.get_top_market_cap_stocks("0000", count=count)

                # ✅ 리스트이므로 .get() 사용 불가 → 대신 리스트 비어있는지 확인
                if not top_response:
                    print("시가총액 상위 종목 조회 실패: 결과 없음")
                    return running_status

                # ✅ 리스트에서 종목코드 추출
                top_stock_codes = [
                    item["code"]
                    for item in top_response[:count]
                    if "code" in item
                ]

                # 백테스트 모드 전략 구성
                strategy = MomentumStrategy(
                    broker=self.broker,
                    min_change_rate=10.0,
                    min_follow_through=3.0,
                    min_follow_through_time=10,  # 10분 후 상승률 기준으로 판단
                    mode="backtest",
                    backtest_lookup=self._mock_backtest_price_lookup,
                    logger=self.logger
                )
                executor = StrategyExecutor(strategy)
                result = await executor.execute(top_stock_codes)

                print("\n📊 [백테스트 결과]")
                print("✔️ Follow Through 종목:")
                for item in result["follow_through"]:
                    print(f" - {item['name']}({item['code']})")

                print("❌ Follow 실패 종목:")
                for item in result["not_follow_through"]:
                    print(f" - {item['name']}({item['code']})")

            except Exception as e:
                self.logger.error(f"[백테스트] 전략 실행 중 오류 발생: {e}")
                print(f"[오류] 전략 실행 실패: {e}")

        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")

        return running_status

    async def run_async(self):
        """애플리케이션의 메인 비동기 루프를 실행합니다."""

        # 애플리케이션 시작 시 환경 선택
        if not await self._select_environment():
            self.logger.critical("거래 환경 초기화 실패. 애플리케이션을 종료합니다.")
            return

        running = True
        while running:
            self._display_menu()
            choice = await asyncio.to_thread(input, "원하는 작업을 선택하세요 (숫자 입력): ")
            choice = choice.strip()
            running = await self._execute_action(choice)
            if running:
                await asyncio.to_thread(input, "계속하려면 Enter를 누르세요...")

    async def _mock_backtest_price_lookup(self, stock_code: str) -> int:
        """
        백테스트용으로 주가 상승을 가정한 모의 가격 제공
        (실제로는 DB, CSV, 또는 API를 통해 특정 시점 데이터를 받아야 함)
        """
        try:
            current_info = await self.api_client.quotations.get_price_summary(stock_code)
            return int(current_info["current"] * 1.05)  # 5% 상승 가정
        except Exception as e:
            self.logger.warning(f"[백테스트] {stock_code} 가격 조회 실패: {e}")
            return 0
