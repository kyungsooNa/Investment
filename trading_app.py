# trading_app.py
import os
from core.config_loader import load_config
from api.env import KoreaInvestEnv
from api.client import KoreaInvestAPI


class TradingApp:
    def __init__(self, main_config_path, tr_ids_config_path):
        self.main_config_path = main_config_path
        self.tr_ids_config_path = tr_ids_config_path

        self.env = None
        self.api_client = None

        self._initialize_api_client()

    def _initialize_api_client(self):
        """환경 설정 로드, 토큰 발급 및 API 클라이언트 초기화."""
        try:
            # 설정 파일 로드 및 병합
            main_config_data = load_config(self.main_config_path)
            tr_ids_data = load_config(self.tr_ids_config_path)

            config_data = {}
            config_data.update(main_config_data)
            config_data.update(tr_ids_data)

            self.env = KoreaInvestEnv(config_data)

            # 접근 토큰 발급
            access_token = self.env.get_access_token()
            if not access_token:
                print("ERROR: API 접근 토큰 발급에 실패했습니다. config.yaml 설정을 확인하세요.")
                raise Exception("API Token Issuance Failed")

            # API 클라이언트 초기화
            self.api_client = KoreaInvestAPI(self.env)  # env 객체 전달
            print(f"\n성공적으로 API 클라이언트를 초기화했습니다: {self.api_client}")
        except FileNotFoundError as e:
            print(f"ERROR: 설정 파일을 찾을 수 없습니다: {e}")
            raise
        except Exception as e:
            print(f"ERROR: 애플리케이션 초기화 실패: {e}")
            raise

    def _display_menu(self):
        """사용자에게 메뉴 옵션을 출력합니다."""
        print("\n--- 한국투자증권 API 애플리케이션 ---")
        print("1. 주식 현재가 조회 (삼성전자)")
        print("2. 계좌 잔고 조회")
        print("3. 주식 매수 주문 (삼성전자 1주, 지정가)")
        print("4. 시가총액 상위 종목 조회 (모의투자 미지원)")
        print("0. 종료")
        print("-----------------------------------")

    def _execute_action(self, choice):
        """사용자 선택에 따라 해당 작업을 실행합니다."""
        if choice == '1':
            self._get_current_stock_price("005930")  # 삼성전자
        elif choice == '2':
            self._get_account_balance()
        elif choice == '3':
            self._place_buy_order("005930", "58500", "1", "00")  # 종목코드, 가격, 수량, 주문유형
        elif choice == '4':
            self._get_top_market_cap_stocks("0000")
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            return False
        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")
        return True

    def _get_current_stock_price(self, stock_code):
        """주식 현재가 조회 로직."""
        print(f"\n--- {stock_code} 현재가 조회 ---")
        current_price_result = self.api_client.quotations.get_current_price(stock_code)
        if current_price_result and current_price_result.get('rt_cd') == '0':
            print(f"\n{stock_code} 현재가: {current_price_result}")
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")

    def _get_account_balance(self):
        """계좌 잔고 조회 로직 (실전/모의 구분)."""
        print("\n--- 계좌 잔고 조회 ---")
        if self.env.is_paper_trading:
            print("INFO: 모의투자 계좌 잔고를 조회합니다.")
            account_balance = self.api_client.account.get_account_balance()
        else:
            print("INFO: 실전 계좌 잔고를 조회합니다.")
            account_balance = self.api_client.account.get_real_account_balance()

        if account_balance and account_balance.get('rt_cd') == '0':
            print(f"\n계좌 잔고: {account_balance}")
        else:
            print(f"\n계좌 잔고 조회 실패.")

    def _place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문 로직."""
        print("\n--- 주식 매수 주문 시도 ---")
        buy_order_result = self.api_client.trading.place_stock_order(
            stock_code,
            price,
            qty,
            "매수",
            order_dvsn
        )
        if buy_order_result and buy_order_result.get('rt_cd') == '0':
            print(f"주식 매수 주문 성공: {buy_order_result}")
        else:
            print(f"주식 매수 주문 실패: {buy_order_result}")

    def _get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목 조회 로직 (모의투자 미지원)."""
        print("\n--- 시가총액 상위 종목 조회 시도 (모의투자 미지원) ---")
        if self.env.is_paper_trading:
            print("WARNING: 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다. config.yaml의 is_paper_trading을 False로 설정해주세요.")
            return

        top_market_cap_stocks = self.api_client.quotations.get_top_market_cap_stocks(market_code)

        if top_market_cap_stocks and top_market_cap_stocks.get('rt_cd') == '0':
            print(f"성공: 시가총액 상위 종목 목록:")
            for stock_info in top_market_cap_stocks.get('output', []):
                print(f"  순위: {stock_info.get('data_rank', '')}, "
                      f"종목명: {stock_info.get('hts_kor_isnm', '')}, "
                      f"시가총액: {stock_info.get('stck_avls', '')}, "
                      f"현재가: {stock_info.get('stck_prpr', '')}")
        else:
            print(f"실패: 시가총액 상위 종목 조회.")

    def run(self):
        """애플리케이션의 메인 루프를 실행합니다."""
        running = True
        while running:
            self._display_menu()
            choice = input("원하는 작업을 선택하세요 (숫자 입력): ").strip()
            running = self._execute_action(choice)
            if running:  # 작업 실행 후 바로 종료되지 않았다면 잠시 대기
                input("계속하려면 Enter를 누르세요...")  # 사용자가 결과를 확인할 수 있도록 일시 정지