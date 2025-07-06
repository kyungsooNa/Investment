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
        return await asyncio.to_thread(input, prompt)

    def display_current_time(self):
        """현재 시각을 표시합니다."""
        current_time = self.time_manager.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
        print(f"현재 시각: {current_time}")

    def display_market_status(self, is_open: bool):
        """시장 개장 여부를 표시합니다."""
        status = "개장" if is_open else "폐장"
        print(f"시장 상태: {status}")

    def display_account_balance(self, balance_info: dict):
        """계좌 잔고 정보를 표시합니다."""
        print("\n--- 계좌 잔고 ---")
        print(f"예수금: {balance_info.get('dnca_tot_amt', 'N/A')}원")
        print(f"총 평가 금액: {balance_info.get('tot_evlu_amt', 'N/A')}원")
        print(f"총 평가 손익: {balance_info.get('tot_evlu_pfls_amt', 'N/A')}원")
        print(f"총 손익률: {balance_info.get('tot_evlu_pfls_rt', 'N/A')}%")
        print("-----------------")

    def display_stock_info(self, stock_summary: dict):
        """단일 종목 정보를 표시합니다."""
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
        if result and result.get('rt_cd') == '0':
            print(f"\n✔️ {action} 성공!")
            print(f"주문 번호: {result.get('ord_no', 'N/A')}")
            print(f"주문 시각: {result.get('ord_tmd', 'N/A')}")
        else:
            print(f"\n❌ {action} 실패: {result.get('msg1', '알 수 없는 오류')}")

    def display_app_start_error(self, message: str):
        """애플리케이션 시작 오류 메시지를 표시합니다."""
        print(f"\n[오류] 애플리케이션 시작 실패: {message}")
        print("설정 파일을 확인하거나 관리자에게 문의하세요.")

    def display_strategy_running_message(self, strategy_name: str):
        """전략 실행 시작 메시지를 표시합니다."""
        print(f"\n--- {strategy_name} 전략 실행 시작 ---")

    def display_top_stocks_failure(self, message: str):
        """시가총액 상위 종목 조회 실패 메시지를 표시합니다."""
        print(f"시가총액 상위 종목 조회 실패: {message}")

    def display_top_stocks_success(self):
        """시가총액 상위 종목 조회 성공 메시지를 표시합니다."""
        print("시가총액 상위 종목 조회 완료.")

    def display_no_stocks_for_strategy(self):
        """전략 실행을 위한 종목이 없음을 알립니다."""
        print("전략을 실행할 종목이 없습니다.")

    def display_strategy_results(self, strategy_name: str, results: dict):
        """전략 실행 결과를 요약하여 표시합니다."""
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
        print(f"\n[오류] 전략 실행 중 문제 발생: {message}")

    def display_invalid_menu_choice(self):
        """잘못된 메뉴 선택 메시지를 표시합니다."""
        print("잘못된 메뉴 선택입니다. 다시 시도해주세요.")

    def display_warning_strategy_market_closed(self):
        """시장이 닫혔을 때 전략 실행 경고 메시지를 표시합니다."""
        print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")

    def display_follow_through_stocks(self, stocks: list):
        """Follow Through 종목 목록을 표시합니다."""
        print("✔️ Follow Through 종목:")
        if stocks:
            for s in stocks:
                # 딕셔너리 형태의 종목 정보를 가정
                if isinstance(s, dict):
                    print(f" - {s.get('name', 'N/A')}({s.get('code', 'N/A')})")
                else: # 문자열 형태의 종목 코드만 있을 경우
                    print(f" - {s}")
        else:
            print("   없음")

    def display_not_follow_through_stocks(self, stocks: list):
        """Follow 실패 종목 목록을 표시합니다."""
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
        print("✔️ 후보 종목:")
        if stocks:
            for item in stocks:
                print(f" - {item.get('name', 'N/A')}({item.get('code', 'N/A')})")
        else:
            print("   없음")

    def display_gapup_pullback_rejected_stocks(self, stocks: list):
        """GapUpPullback 전략의 제외 종목 목록을 표시합니다."""
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
        print("애플리케이션을 종료합니다.")

    def display_token_invalidated_message(self):
        """토큰 무효화 메시지를 표시합니다."""
        print("토큰이 무효화되었습니다. 다음 요청 시 새 토큰이 발급됩니다.")

    def display_account_balance_failure(self):
        """계좌 잔고 조회 실패 메시지를 표시합니다."""
        print("계좌 잔고 조회에 실패했습니다.")

    def display_stock_code_not_found(self, stock_name: str):
        """종목 코드를 찾을 수 없을 때 메시지를 표시합니다."""
        print(f"'{stock_name}'에 해당하는 종목 코드를 찾을 수 없습니다.")

    # display_menu 메서드 업데이트 (환경 정보 표시)
    def display_menu(self, env_type: str, current_time_str: str, market_status_str: str):
        """사용자에게 메뉴 옵션을 출력하고 현재 시간을 포함합니다 (환경에 따라 동적)."""
        print(
            f"\n--- 한국투자증권 API 애플리케이션 (환경: {env_type}, 현재: {current_time_str}, 시장: {market_status_str}) ---")
        print("0. 거래 환경 변경")
        print("1. 주식 현재가 조회 (종목코드 입력)") # 수정: 삼성전자 -> 종목코드 입력
        print("2. 계좌 잔고 조회")
        print("3. 주식 매수 주문 (종목코드, 수량, 가격 입력)") # 수정: 삼성전자 1주, 지정가 -> 종목코드, 수량, 가격 입력
        print("4. 주식 매도 주문 (종목코드, 수량, 가격 입력)") # 수정: 실시간 주식 체결가/호가 구독 -> 주식 매도 주문
        print("5. 주식 전일대비 등락률 조회 (종목코드 입력)") # 수정: 삼성전자 -> 종목코드 입력
        print("6. 주식 시가대비 조회 (종목코드 입력)") # 수정: 삼성전자 -> 종목코드 입력
        print("7. 시가총액 상위 종목 조회 (실전전용)")
        print("8. 시가총액 1~10위 종목 현재가 조회 (실전전용)")
        print("9. 상한가 종목 조회 (상위 500개 종목 기준)")
        print("10. 모멘텀 전략 실행 (상승 추세 필터링)")
        print("11. 모멘텀 전략 백테스트 실행")
        print("12. GapUpPullback 전략 실행")
        print("13. 실시간 주식 체결가/호가 구독 (종목코드 입력)")
        print("98. 토큰 무효화") # 14번 메뉴 추가
        print("99. 종료")
        print("-----------------------------------")

    async def select_environment_input(self) -> str:
        """환경 선택 프롬프트를 출력하고 사용자 입력을 받습니다."""
        print("\n--- 거래 환경 선택 ---")
        print("1. 모의투자")
        print("2. 실전투자")
        print("-----------------------")
        # asyncio.to_thread를 사용하여 동기 input() 함수를 비동기 환경에서 실행
        choice = (await asyncio.to_thread(input, "환경을 선택하세요 (숫자 입력): ")).strip()
        return choice
