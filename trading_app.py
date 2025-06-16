# trading_app.py
import os
from core.config_loader import load_config
from api.env import KoreaInvestEnv
from api.client import KoreaInvestAPI
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger  # Logger 클래스 임포트


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
        self.logger = Logger()  # Logger 인스턴스 생성

        self._initialize_api_client()

    def _initialize_api_client(self):
        """환경 설정 로드, 토큰 발급 및 API 클라이언트 초기화."""
        try:
            # 기존 print() 대신 logger.info() 사용
            self.logger.info("애플리케이션 초기화 시작...")

            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.env = KoreaInvestEnv(config_data, self.logger)  # env에도 logger 전달

            # TimeManager 초기화
            self.time_manager = TimeManager(
                market_open_time=config_data.get('market_open_time', "09:00"),
                market_close_time=config_data.get('market_close_time', "15:30"),
                timezone=config_data.get('market_timezone', "Asia/Seoul")
            )

            access_token = self.env.get_access_token()
            if not access_token:
                raise Exception("API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

            # API 클라이언트 및 서비스 계층 초기화
            self.api_client = KoreaInvestAPI(self.env, self.logger)  # client에도 logger 전달
            self.trading_service = TradingService(self.api_client, self.env, self.logger)  # service에도 logger 전달

            self.logger.info(f"API 클라이언트 초기화 성공: {self.api_client}")
        except FileNotFoundError as e:
            self.logger.error(f"설정 파일을 찾을 수 없습니다: {e}")
            raise
        except Exception as e:
            self.logger.critical(f"애플리케이션 초기화 실패: {e}")
            raise

    def _display_menu(self):
        """사용자에게 메뉴 옵션을 출력하고 현재 시간을 포함합니다."""
        current_time = self.time_manager.get_current_kst_time()
        # is_market_open()은 이제 내부적으로 logger를 사용합니다.
        market_open_status = self.time_manager.is_market_open()  # True/False 반환
        market_status_str = "열려있음" if market_open_status else "닫혀있음"

        print(
            f"\n--- 한국투자증권 API 애플리케이션 (현재: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 시장: {market_status_str}) ---")
        print("1. 주식 현재가 조회 (삼성전자)")
        print("2. 계좌 잔고 조회")
        print("3. 주식 매수 주문 (삼성전자 1주, 지정가)")
        print("4. 시가총액 상위 종목 조회 (모의투자 미지원)")
        print("0. 종료")
        print("-----------------------------------")

    def _execute_action(self, choice):
        """사용자 선택에 따라 해당 작업을 실행하고 결과를 콘솔에 출력합니다."""
        if choice == '1':
            self._handle_get_current_stock_price("005930")
        elif choice == '2':
            self._handle_get_account_balance()
        elif choice == '3':
            # 주문 전 시장 개장 여부 확인
            if not self.time_manager.is_market_open():  # is_market_open()은 이미 로그를 남김
                print("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.")  # CLI에 경고 출력
            else:
                self._handle_place_buy_order("005930", "58500", "1", "00")
        elif choice == '4':
            self._handle_get_top_market_cap_stocks("0000")
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            return False
        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")
        return True

    def _handle_get_current_stock_price(self, stock_code):
        """현재가 조회 요청 및 결과 출력."""
        print(f"\n--- {stock_code} 현재가 조회 ---")  # CLI 출력 유지
        current_price_result = self.trading_service.get_current_stock_price(stock_code)
        if current_price_result and current_price_result.get('rt_cd') == '0':
            print(f"\n{stock_code} 현재가: {current_price_result}")
            self.logger.info(f"{stock_code} 현재가 조회 성공.")  # 시스템 로그
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result}")  # 시스템 로그

    def _handle_get_account_balance(self):
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")  # CLI 출력 유지
        account_balance = self.trading_service.get_account_balance()
        if account_balance and account_balance.get('rt_cd') == '0':
            print(f"\n계좌 잔고: {account_balance}")
            self.logger.info("계좌 잔고 조회 성공.")  # 시스템 로그
        else:
            print(f"\n계좌 잔고 조회 실패.")
            self.logger.error(f"계좌 잔고 조회 실패: {account_balance}")  # 시스템 로그

    def _handle_place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문 요청 및 결과 출력."""
        print("\n--- 주식 매수 주문 시도 ---")  # CLI 출력 유지
        buy_order_result = self.trading_service.place_buy_order(
            stock_code, price, qty, order_dvsn
        )
        if buy_order_result and buy_order_result.get('rt_cd') == '0':
            print(f"주식 매수 주문 성공: {buy_order_result}")
            self.logger.info(f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}")  # 시스템 로그
        else:
            print(f"주식 매수 주문 실패: {buy_order_result}")
            self.logger.error(f"주식 매수 주문 실패: 종목={stock_code}, 결과={buy_order_result}")  # 시스템 로그

    def _get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목 조회 요청 및 결과 출력."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")  # CLI 출력 유지
        top_market_cap_stocks = self.trading_service.get_top_market_cap_stocks(market_code)

        if top_market_cap_stocks and top_market_cap_stocks.get('rt_cd') == '0':
            print(f"성공: 시가총액 상위 종목 목록:")
            for stock_info in top_market_cap_stocks.get('output', []):
                print(f"  순위: {stock_info.get('data_rank', '')}, "
                      f"종목명: {stock_info.get('hts_kor_isnm', '')}, "
                      f"시가총액: {stock_info.get('stck_avls', '')}, "
                      f"현재가: {stock_info.get('stck_prpr', '')}")
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}).")  # 시스템 로그
        else:
            print(f"실패: 시가총액 상위 종목 조회.")
            self.logger.error(f"시가총액 상위 종목 조회 실패: {top_market_cap_stocks}")  # 시스템 로그

    def run(self):
        """애플리케이션의 메인 루프를 실행합니다."""
        running = True
        while running:
            self._display_menu()
            choice = input("원하는 작업을 선택하세요 (숫자 입력): ").strip()
            running = self._execute_action(choice)
            if running:
                input("계속하려면 Enter를 누르세요...")
