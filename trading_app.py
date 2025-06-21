# trading_app.py
import os
from core.config_loader import load_config
from api.env import KoreaInvestEnv
from api.client import KoreaInvestAPI
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
import asyncio  # 비동기 sleep을 위해 필요

# 새로 분리된 핸들러 클래스 임포트
from app.data_handlers import DataHandlers
from app.transaction_handlers import TransactionHandlers


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

    def _load_configs_and_init_env(self):
        """환경 설정 파일 로드 및 KoreaInvestEnv 초기화."""
        try:
            self.logger.info("애플리케이션 초기화 시작...")

            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.env = KoreaInvestEnv(config_data, self.logger)

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

            # API 클라이언트 및 서비스 계층 초기화
            self.api_client = KoreaInvestAPI(self.env, self.logger)
            self.trading_service = TradingService(self.api_client, self.env, self.logger, self.time_manager)

            # 핸들러 클래스 인스턴스화 (서비스와 로거, 타임 매니저 주입)
            self.data_handlers = DataHandlers(self.trading_service, self.logger, self.time_manager)
            self.transaction_handlers = TransactionHandlers(self.trading_service, self.logger, self.time_manager)

            self.logger.info(f"API 클라이언트 및 서비스 초기화 성공: {self.api_client}")
            return True
        except Exception as e:
            self.logger.critical(f"API 클라이언트 초기화 실패: {e}")
            # print(f"ERROR: API 클라이언트 초기화 중 오류 발생: {e}") # CLI에 직접 에러 표시
            return False

    async def _select_environment(self):
        """애플리케이션 시작 시 모의/실전 투자 환경을 선택합니다."""
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

        if not await self._complete_api_initialization():
            print("ERROR: 선택된 환경으로 API 클라이언트 초기화에 실패했습니다. 프로그램을 종료합니다.")
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

        if not self.env.is_paper_trading:
            print("7. 시가총액 상위 종목 조회 (실전전용)")
            print("8. 시가총액 1~10위 종목 현재가 조회 (실전전용)")

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
        elif choice == '4':  # 실시간 주식 체결가/호가 구독 (이전 6번)
            await self.transaction_handlers.handle_realtime_price_quote_stream("005930")
        elif choice == '5':  # 주식 전일대비 등락률 조회 (이전 7번)
            await self.data_handlers.handle_display_stock_change_rate("005930")
        elif choice == '6':  # 주식 시가대비 조회 (이전 8번)
            await self.data_handlers.handle_display_stock_vs_open_price("005930")
        elif choice == '7':  # 시가총액 상위 종목 조회 (이전 4번)
            if self.env.is_paper_trading:
                print("WARNING: 모의투자 환경에서는 시가총액 상위 종목 조회를 지원하지 않습니다.")
                self.logger.warning("모의투자 환경에서 시가총액 상위 종목 조회 시도 (미지원).")
            else:
                await self.data_handlers.handle_get_top_market_cap_stocks("0000")
        elif choice == '8':  # 시가총액 1~10위 종목 현재가 조회 (이전 5번)
            if self.env.is_paper_trading:
                print("WARNING: 모의투자 환경에서는 시가총액 1~10위 종목 조회를 지원하지 않습니다.")
                self.logger.warning("모의투자 환경에서 시가총액 1~10위 종목 조회 시도 (미지원).")
                running_status = True
            else:
                if await self.data_handlers.handle_get_top_10_market_cap_stocks_with_prices():
                    running_status = False
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            running_status = False
        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")

        return running_status

        # --- _handle_로 시작하는 모든 메서드들은 app/data_handlers.py 또는 app/transaction_handlers.py로 이동 ---

    # 이 클래스에서는 더 이상 이 메서드들을 직접 정의하지 않습니다.

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
