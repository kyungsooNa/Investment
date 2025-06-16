# trading_app.py
import os
from core.config_loader import load_config
from api.env import KoreaInvestEnv
from api.client import KoreaInvestAPI
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger


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

        self._initialize_api_client()

    def _initialize_api_client(self):
        """환경 설정 로드, 토큰 발급 및 API 클라이언트 초기화."""
        try:
            self.logger.info("애플리케이션 초기화 시작...")

            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.env = KoreaInvestEnv(config_data, self.logger)

            # TimeManager 초기화
            self.time_manager = TimeManager(
                market_open_time=config_data.get('market_open_time', "09:00"),
                market_close_time=config_data.get('market_close_time', "15:30"),
                timezone=config_data.get('market_timezone', "Asia/Seoul"),
                logger=self.logger
            )

            access_token = self.env.get_access_token()
            if not access_token:
                raise Exception("API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")

            self.api_client = KoreaInvestAPI(self.env, self.logger)
            self.trading_service = TradingService(self.api_client, self.env, self.logger, self.time_manager)

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
        market_open_status = self.time_manager.is_market_open()
        market_status_str = "열려있음" if market_open_status else "닫혀있음"

        print(
            f"\n--- 한국투자증권 API 애플리케이션 (현재: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z%z')}, 시장: {market_status_str}) ---")
        print("1. 주식 현재가 조회 (삼성전자)")
        print("2. 계좌 잔고 조회")
        print("3. 주식 매수 주문 (삼성전자 1주, 지정가)")
        print("4. 시가총액 상위 종목 조회 (모의투자 미지원)")
        print("5. 시가총액 1~10위 종목 현재가 조회")
        print("0. 종료")
        print("-----------------------------------")

    def _execute_action(self, choice):
        """사용자 선택에 따라 해당 작업을 실행하고 결과를 콘솔에 출력합니다."""
        running_status = True  # 기본적으로 루프를 계속 실행

        if choice == '1':
            self._handle_get_current_stock_price("005930")
        elif choice == '2':
            self._handle_get_account_balance()
        elif choice == '3':
            if not self.time_manager.is_market_open():
                print("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.")
            else:
                self._handle_place_buy_order("005930", "58500", "1", "00")
        elif choice == '4':
            self._handle_get_top_market_cap_stocks("0000")
        elif choice == '5':
            # 시가총액 1~10위 조회 후, 성공 시 프로그램 종료
            if self._handle_get_top_10_market_cap_stocks_with_prices():
                running_status = False  # 성공했으면 루프 종료
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            running_status = False
        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")

        return running_status  # 루프의 계속 여부 반환

    def _handle_get_current_stock_price(self, stock_code):
        """현재가 조회 요청 및 결과 출력."""
        print(f"\n--- {stock_code} 현재가 조회 ---")  # CLI 출력 유지
        current_price_result = self.trading_service.get_current_stock_price(stock_code)
        if current_price_result and current_price_result.get('rt_cd') == '0':
            print(f"\n{stock_code} 현재가: {current_price_result}")  # CLI에 전체 결과 출력
            self.logger.info(f"{stock_code} 현재가 조회 성공: {current_price_result}")  # <--- 변경: 결과값 포함
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result}")

    def _handle_get_account_balance(self):
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")  # CLI 출력 유지
        account_balance = self.trading_service.get_account_balance()  # 서비스 호출
        if account_balance and account_balance.get('rt_cd') == '0':
            print(f"\n계좌 잔고: {account_balance}")
            self.logger.info(f"계좌 잔고 조회 성공: {account_balance}")  # <--- 변경: 결과값 포함
        else:
            print(f"\n계좌 잔고 조회 실패.")
            self.logger.error(f"계좌 잔고 조회 실패: {account_balance}")

    def _handle_place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문 요청 및 결과 출력."""
        print("\n--- 주식 매수 주문 시도 ---")  # CLI 출력 유지
        buy_order_result = self.trading_service.place_buy_order(
            stock_code, price, qty, order_dvsn
        )
        if buy_order_result and buy_order_result.get('rt_cd') == '0':
            print(f"주식 매수 주문 성공: {buy_order_result}")
            self.logger.info(f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={buy_order_result}")  # <--- 변경: 결과값 포함
        else:
            print(f"주식 매수 주문 실패: {buy_order_result}")
            self.logger.error(f"주식 매수 주문 실패: 종목={stock_code}, 결과={buy_order_result}")

    def _get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목 조회 요청 및 결과 출력 (전체 목록)."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")  # CLI 출력 유지
        top_market_cap_stocks = self.trading_service.get_top_market_cap_stocks(market_code)

        if top_market_cap_stocks and top_market_cap_stocks.get('rt_cd') == '0':
            print(f"성공: 시가총액 상위 종목 목록:")
            for stock_info in top_market_cap_stocks.get('output', []):
                print(f"  순위: {stock_info.get('data_rank', '')}, "
                      f"종목명: {stock_info.get('hts_kor_isnm', '')}, "
                      f"시가총액: {stock_info.get('stck_avls', '')}, "
                      f"현재가: {stock_info.get('stck_prpr', '')}")
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}), 결과: {top_market_cap_stocks}")  # <--- 변경: 결과값 포함
        else:
            print(f"실패: 시가총액 상위 종목 조회.")
            self.logger.error(f"시가총액 상위 종목 조회 실패: {top_market_cap_stocks}")

    def _handle_get_top_10_market_cap_stocks_with_prices(self):
        """
        시가총액 1~10위 종목의 현재가를 조회하고 출력하는 핸들러.
        성공 시 True, 실패 시 False 반환 (앱 종료 여부 판단용).
        """
        print("\n--- 시가총액 1~10위 종목 현재가 조회 시도 ---")
        top_10_with_prices = self.trading_service.get_top_10_market_cap_stocks_with_prices()

        if top_10_with_prices:  # 서비스 계층에서 성공적으로 리스트가 반환되었다면
            print("\n성공: 시가총액 1~10위 종목 현재가:")
            for stock in top_10_with_prices:
                print(
                    f"  순위: {stock['rank']}, 종목명: {stock['name']}, 종목코드: {stock['code']}, 현재가: {stock['current_price']}원")
            self.logger.info(f"시가총액 1~10위 종목 현재가 조회 성공: {top_10_with_prices}")  # <--- 변경: 결과값 포함
            return True  # 성공했으므로 True 반환
        else:
            print("\n실패: 시가총액 1~10위 종목 현재가 조회.")
            self.logger.error("시가총액 1~10위 종목 현재가 조회 실패.")
            return False  # 실패했으므로 False 반환

    def run(self):
        """애플리케이션의 메인 루프를 실행합니다."""
        running = True
        while running:
            self._display_menu()
            choice = input("원하는 작업을 선택하세요 (숫자 입력): ").strip()
            running = self._execute_action(choice)
            if running:
                input("계속하려면 Enter를 누르세요...")
