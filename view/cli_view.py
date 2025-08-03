import asyncio
from core.logger import Logger
from core.time_manager import TimeManager

class CLIView:
    """
    명령줄 인터페이스(CLI)를 통해 사용자에게 정보를 표시하고 입력을 받는 클래스입니다.
    향후 GUI나 웹 뷰로 확장될 수 있도록 콘솔 출력 로직을 캡슐화합니다.
    """
    def __init__(self, time_manager: TimeManager, logger: Logger):
        self.time_manager = time_manager
        self.logger = logger

    def display_welcome_message(self):
        """환영 메시지를 표시합니다."""
        print("**************************************************")
        print("********* 파이썬 증권 자동매매 시스템 *********")
        print("**************************************************")

    async def get_user_input(self, prompt: str) -> str:
        """사용자로부터 입력을 받습니다."""
        self._print_time_header()
        return await asyncio.to_thread(input, prompt)

    def display_current_time(self):
        """현재 시각을 표시합니다."""
        current_time = self.time_manager.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
        print(f"현재 시각: {current_time}")

    def display_market_status(self, is_open: bool):
        """시장 개장 여부를 표시합니다."""
        self._print_time_header()
        status = "개장" if is_open else "폐장"
        print(f"시장 상태: {status}")

    def display_account_balance(self, balance_info: dict):
        """계좌 잔고 정보를 표시합니다."""
        try:
            self._print_time_header()
            print("\n--- 계좌 잔고 ---")
            print(f"계좌번호1: {balance_info.get('ctx_area_fk100')}")
            print(f"계좌번호2: {balance_info.get('ctx_area_nk100')}")
            output1 = balance_info.get('output1')
            output2 = balance_info.get('output2')[0]
            print(f"예수금: {output2.get('dnca_tot_amt', 'N/A')}원")
            print(f"총 평가 금액: {output2.get('tot_evlu_amt', 'N/A')}원")
            print(f"총 평가 손익: {output2.get('tot_evlu_pfls_amt', 'N/A')}원")
            print(f"총 손익률: {output2.get('tot_evlu_pfls_rt', 'N/A')}%")
            print("-----------------")
        except (IndexError, TypeError):
            print("계좌 상세 내역이 없습니다.")


    def display_stock_info(self, stock_summary: dict):
        """단일 종목 정보를 표시합니다."""
        self._print_time_header()
        if stock_summary:
            print("\n--- 종목 정보 ---")
            print(f"종목명: {stock_summary.get('name', 'N/A')}")
            print(f"현재가: {stock_summary.get('current', 'N/A')}원")
            print(f"전일 대비: {stock_summary.get('diff', 'N/A')}원 ({stock_summary.get('diff_rate', 'N/A')}%)")
            print(f"거래량: {stock_summary.get('volume', 'N/A')}")
            print("-----------------")
        else:
            print("종목 정보를 찾을 수 없습니다.")

    def display_transaction_result(self, result: dict, action: str):
        """매수/매도 거래 결과를 표시합니다."""
        self._print_time_header()
        if result and result.get('rt_cd') == '0':
            print(f"\n✔️ {action} 성공!")
            print(f"주문 번호: {result.get('ord_no', 'N/A')}")
            print(f"주문 시각: {result.get('ord_tmd', 'N/A')}")
        else:
            print(f"\n❌ {action} 실패: {result.get('msg1', '알 수 없는 오류')}")

    def display_app_start_error(self, message: str):
        """애플리케이션 시작 오류 메시지를 표시합니다."""
        self._print_time_header()
        print(f"\n[오류] 애플리케이션 시작 실패: {message}")
        print("설정 파일을 확인하거나 관리자에게 문의하세요.")

    def display_strategy_running_message(self, strategy_name: str):
        """전략 실행 시작 메시지를 표시합니다."""
        self._print_time_header()
        print(f"\n--- {strategy_name} 전략 실행 시작 ---")

    def display_top_stocks_failure(self, message: str):
        """시가총액 상위 종목 조회 실패 메시지를 표시합니다."""
        self._print_time_header()
        print(f"시가총액 상위 종목 조회 실패: {message}")

    def display_top_stocks_success(self):
        """시가총액 상위 종목 조회 성공 메시지를 표시합니다."""
        self._print_time_header()
        print("시가총액 상위 종목 조회 완료.")

    def display_no_stocks_for_strategy(self):
        """전략 실행을 위한 종목이 없음을 알립니다."""
        self._print_time_header()
        print("전략을 실행할 종목이 없습니다.")

    def display_strategy_results(self, strategy_name: str, results: dict):
        """전략 실행 결과를 요약하여 표시합니다."""
        self._print_time_header()
        print(f"\n--- {strategy_name} 전략 실행 결과 ---")
        print(f"총 처리 종목: {results.get('total_processed', 0)}개")
        print(f"매수 시도 종목: {results.get('buy_attempts', 0)}개")
        print(f"매수 성공 종목: {results.get('buy_successes', 0)}개")
        print(f"매도 시도 종목: {results.get('sell_attempts', 0)}개")
        print(f"매도 성공 종목: {results.get('sell_successes', 0)}개")
        execution_time_value = results.get('execution_time', 0.0)
        # 값이 숫자 타입이 아닐 경우 0.0으로 강제 변환하여 포맷팅 오류 방지
        if not isinstance(execution_time_value, (int, float)):
            execution_time_value = 0.0
        print(f"전략 실행 시간: {execution_time_value:.2f}초")
        print("---------------------------------")

    def display_strategy_error(self, message: str):
        """전략 실행 중 오류 메시지를 표시합니다."""
        self._print_time_header()
        print(f"\n[오류] 전략 실행 중 문제 발생: {message}")

    def display_invalid_menu_choice(self):
        """잘못된 메뉴 선택 메시지를 표시합니다."""
        self._print_time_header()
        print("잘못된 메뉴 선택입니다. 다시 시도해주세요.")

    def display_warning_strategy_market_closed(self):
        """시장이 닫혔을 때 전략 실행 경고 메시지를 표시합니다."""
        self._print_time_header()
        print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")

    def display_follow_through_stocks(self, stocks: list):
        """Follow Through 종목 목록을 표시합니다."""
        self._print_time_header()
        print("✔️ Follow Through 종목:")
        if stocks:
            for s in stocks:
                # 딕셔너리 형태의 종목 정보를 가정
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:  # 문자열 형태의 종목 코드만 있을 경우
                    print(f" - {s}")
        else:
            print("   없음")

    def display_not_follow_through_stocks(self, stocks: list):
        """Follow 실패 종목 목록을 표시합니다."""
        self._print_time_header()
        print("❌ Follow 실패 종목:")
        if stocks:
            for s in stocks:
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else:
                    print(f" - {s}")
        else:
            print("   없음")

    def display_gapup_pullback_selected_stocks(self, stocks: list):
        """GapUpPullback 전략의 후보 종목 목록을 표시합니다."""
        self._print_time_header()
        print("✔️ 후보 종목:")
        if stocks:
            for item in stocks:
                print(f" - {item.get('name', 'N/A')}({item.get('code', 'N/A')}) - 등락률 ({item.get('prdy_ctrt', 'N/A')})")
        else:
            print("   없음")

    def display_gapup_pullback_rejected_stocks(self, stocks: list):
        """GapUpPullback 전략의 제외 종목 목록을 표시합니다."""
        self._print_time_header()
        print("❌ 제외 종목:")
        if stocks:
            for item in stocks:
                print(f" - {item.get('name', 'N/A')}({item.get('code', 'N/A')})")
        else:
            print("   없음")

    def display_invalid_input_warning(self, message: str):
        """사용자 입력 경고 메시지를 표시합니다."""
        print(f"WARNING: {message}")

    def display_exit_message(self):
        """종료 메시지를 표시합니다."""
        self._print_time_header()
        print("애플리케이션을 종료합니다.")

    def display_token_invalidated_message(self):
        """토큰 무효화 메시지를 표시합니다."""
        self._print_time_header()
        print("토큰이 무효화되었습니다. 다음 요청 시 새 토큰이 발급됩니다.")

    def display_account_balance_failure(self, msg):
        """계좌 잔고 조회 실패 메시지를 표시합니다."""
        self._print_time_header()
        print(f"계좌 잔고 조회에 실패했습니다.{msg}")

    def display_stock_code_not_found(self, stock_name: str):
        """종목 코드를 찾을 수 없을 때 메시지를 표시합니다."""
        self._print_time_header()
        print(f"'{stock_name}'에 해당하는 종목 코드를 찾을 수 없습니다.")

    def display_menu(self, env_type: str, current_time_str: str, market_status_str: str, menu_items: dict):
        """
        사용자에게 메뉴 옵션을 동적으로 출력하고 현재 상태를 표시합니다.

        Args:
            env_type (str): 현재 거래 환경 (예: "모의투자")
            current_time_str (str): 현재 시각 문자열
            market_status_str (str): 시장 개장 상태 문자열 (예: "열려있음")
            menu_items (dict): 카테고리별로 그룹화된 메뉴 항목 딕셔너리
        """
        self._print_time_header()
        print(
            f"\n--- 한국투자증권 API 애플리케이션 (환경: {env_type}, 현재: {current_time_str}, 시장: {market_status_str}) ---")

        # 딕셔너리를 순회하며 동적으로 메뉴를 생성
        for category, items in menu_items.items():
            print(f"\n[{category}]")
            for number, description in items.items():
                # 숫자를 오른쪽 정렬하여 가독성 향상
                print(f"  {number.rjust(3)}. {description}")

        print("-----------------------------------------------------------------")

    async def select_environment_input(self) -> str:
        """환경 선택 프롬프트를 출력하고 사용자 입력을 받습니다."""
        self._print_time_header()
        print("\n--- 거래 환경 선택 ---")
        print("1. 모의투자")
        print("2. 실전투자")
        print("-----------------------")
        # asyncio.to_thread를 사용하여 동기 input() 함수를 비동기 환경에서 실행
        choice = (await asyncio.to_thread(input, "환경을 선택하세요 (숫자 입력): ")).strip()
        return choice

    def display_warning_paper_trading_not_supported(self, msg):
        self._print_time_header()
        print(f"\"{msg}\"는 실전 전용 기능입니다.")

    def display_invalid_environment_choice(self, msg):
        self._print_time_header()
        print(f"\"{msg}\" 잘못된 환경 선택입니다.")

    def _print_time_header(self):
        """현재 시각을 출력하는 공통 헤더."""
        current_time = self.time_manager.get_current_kst_time().strftime("[%Y-%m-%d %H:%M:%S]")
        print(current_time)
