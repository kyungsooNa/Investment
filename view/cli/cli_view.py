import asyncio

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from common.types import ResFluctuation
from config.DynamicConfig import DynamicConfig
from core.logger import Logger
from core.time_manager import TimeManager


class CLIView:
    """
    명령줄 인터페이스(CLI)를 통해 사용자에게 정보를 표시하고 입력을 받는 클래스입니다.
    향후 GUI나 웹 뷰로 확장될 수 있도록 콘솔 출력 로직을 캡슐화합니다.
    """

    def __init__(self, env: KoreaInvestApiEnv, time_manager: TimeManager, logger: Logger):
        self.time_manager = time_manager
        self.logger = logger
        self.env = env

    def _print_common_header(self):
        self._print_time_header()
        self._print_current_mode()

    def _print_current_mode(self):
        """현재 모드를 출력하는 공통 헤더."""
        env = self.env
        if env.is_paper_trading is None:
            mode = "None"
        elif env.is_paper_trading is True:
            mode = "모의투자"
        else:
            mode = "실전투자"
        print(f"\n=== 현재 모드: [{mode}] ===")

    def _print_time_header(self):
        """현재 시각을 출력하는 공통 헤더."""
        current_time = self.time_manager.get_current_kst_time().strftime("[%Y-%m-%d %H:%M:%S]")
        print(current_time)

    def display_welcome_message(self):
        """환영 메시지를 표시합니다."""
        print("**************************************************")
        print("********* 파이썬 증권 자동매매 시스템 *********")
        print("**************************************************")

    async def get_user_input(self, prompt: str) -> str:
        """사용자로부터 입력을 받습니다."""
        self._print_common_header()
        return await asyncio.to_thread(input, prompt)

    def display_current_time(self):
        """현재 시각을 표시합니다."""
        current_time = self.time_manager.get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S")
        print(f"현재 시각: {current_time}")

    def display_market_status(self, is_open: bool):
        """시장 개장 여부를 표시합니다."""
        self._print_common_header()
        status = "개장" if is_open else "폐장"
        print(f"시장 상태: {status}")

    def display_account_balance(self, balance_info: dict):
        """계좌 잔고 정보를 표시합니다."""
        try:
            self._print_common_header()

            # ✅ 계좌번호 출력
            account_number = self.env.active_config.get("stock_account_number", "N/A")
            print(f"\n📒 계좌번호: {account_number}")

            output1 = balance_info.get('output1', [])
            output2 = balance_info.get('output2', [])

            if not output2:
                print("게좌 정보가 없습니다.")
                return

            asset_info = output2[0]

            # 계좌 요약
            print("\n--- 계좌 요약 ---")
            print(f"예수금: {int(asset_info.get('dnca_tot_amt', 0)):,}원")
            print(f"총 평가 금액: {int(asset_info.get('tot_evlu_amt', 0)):,}원")
            print(f"총 평가 손익: {int(asset_info.get('evlu_pfls_smtl_amt', 0)):,}원")
            print(f"총 수익률: {float(asset_info.get('asst_icdc_erng_rt', 0)):.4%}")
            print(f"당일 매수 금액: {int(asset_info.get('thdt_buy_amt', 0)):,}원")
            print(f"당일 매도 금액: {int(asset_info.get('thdt_sll_amt', 0)):,}원")

            if not output1:
                print("보유 종목 정보가 없습니다.")
                return

            # 보유 종목
            print("\n--- 보유 종목 목록 ---")
            for idx, stock in enumerate(output1, 1):
                print(f"\n[{idx}] {stock.get('prdt_name', 'N/A')} ({stock.get('pdno', '')})")
                print(f"  - 보유수량: {int(stock.get('hldg_qty', 0)):,}주")
                print(f"  - 주문가능수량: {int(stock.get('ord_psbl_qty', 0)):,}주")
                print(f"  - 평균매입가: {float(stock.get('pchs_avg_pric', 0)):,}원")
                print(f"  - 현재가: {int(stock.get('prpr', 0)):,}원")
                print(f"  - 평가금액: {int(stock.get('evlu_amt', 0)):,}원")
                print(f"  - 평가손익: {int(stock.get('evlu_pfls_amt', 0)):,}원")
                evlu_pfls_amt = int(stock.get('evlu_pfls_amt', 0))
                pchs_amt = int(stock.get('pchs_amt', 1))
                rate = evlu_pfls_amt / pchs_amt * 100 if pchs_amt else 0
                print(f"  - 수익률: {rate:.2f}%")
                print(f"  - 매입금액: {int(stock.get('pchs_amt', 0)):,}원")
                print(f"  - 매매구분: {stock.get('trad_dvsn_name', 'N/A')}")

            print("\n-----------------")

        except (IndexError, TypeError, ValueError) as e:
            print(f"계좌 상세 내역이 없습니다. 오류: {e}")

    def display_stock_info(self, stock_summary: dict):
        """단일 종목 정보를 표시합니다."""
        self._print_common_header()
        if stock_summary:
            print("\n--- 종목 정보 ---")
            print(f"종목명: {stock_summary.get('name', 'N/A')}")
            print(f"현재가: {stock_summary.get('current', 'N/A')}원")
            print(f"전일 대비: {stock_summary.get('diff', 'N/A')}원 ({stock_summary.get('diff_rate', 'N/A')}%)")
            print(f"거래량: {stock_summary.get('volume', 'N/A')}")
            print("-----------------")
        else:
            print("종목 정보를 찾을 수 없습니다.")

    def _label(self, order_type):
        return "매수" if order_type == "buy" else "매도"

    def display_order_success(self, order_type, stock_code, qty, response):
        print(f"\n--- 주식 {self._label(order_type)} 주문 성공 ---")
        print(f"종목={stock_code}, 수량={qty}, 결과={response.data}")
        print(f"주문 번호: {response.data.get('ord_no', 'N/A')}")
        print(f"주문 시각: {response.data.get('ord_tmd', 'N/A')}")

    def display_order_failure(self, order_type, stock_code, response):
        print(f"\n--- 주식 {self._label(order_type)} 주문 실패 ---")
        if response:
            print(f"종목={stock_code}, 결과={response.data}")
        else:
            print("응답이 없습니다.")

    def display_stock_change_rate_success(self, stock_code, current_price, change_val, change_rate):
        print(f"\n--- {stock_code} 전일대비 등락률 조회 ---")
        print(f"성공: {stock_code} ({current_price}원)")
        print(f"  전일대비: {change_val}원")
        print(f"  전일대비율: {change_rate}%")

    def display_stock_change_rate_failure(self, stock_code):
        print(f"\n실패: {stock_code} 전일대비 등락률 조회.")

    def display_stock_vs_open_price_success(self, stock_code, current_price, open_price, vs_val, vs_rate):
        print(f"\n--- {stock_code} 시가대비 조회 ---")
        print(f"성공: {stock_code}")
        print(f"  현재가: {current_price}원")
        print(f"  시가: {open_price}원")
        print(f"  시가대비 등락률: {vs_val}원 ({vs_rate})")

    def display_stock_vs_open_price_failure(self, stock_code):
        print(f"\n실패: {stock_code} 시가대비 조회.")

    def display_transaction_result(self, result: dict, action: str):
        """매수/매도 거래 결과를 표시합니다."""
        self._print_common_header()
        if result and result.get('rt_cd') == '0':
            print(f"\n✔️ {action} 성공!")
            print(f"주문 번호: {result.get('ord_no', 'N/A')}")
            print(f"주문 시각: {result.get('ord_tmd', 'N/A')}")
        else:
            print(f"\n❌ {action} 실패: {result.get('msg1', '알 수 없는 오류')}")

    def display_app_start_error(self, message: str):
        """애플리케이션 시작 오류 메시지를 표시합니다."""
        self._print_common_header()
        print(f"\n[오류] 애플리케이션 시작 실패: {message}")
        print("설정 파일을 확인하거나 관리자에게 문의하세요.")

    def display_strategy_running_message(self, strategy_name: str):
        """전략 실행 시작 메시지를 표시합니다."""
        self._print_common_header()
        print(f"\n--- {strategy_name} 전략 실행 시작 ---")

    # 시총 상위 전체
    def display_top_market_cap_stocks_success(self, items):
        print("\n--- 시가총액 상위 종목 목록 ---")
        if not items:
            print("시가총액 상위 종목이 없습니다.")
            return
        for s in items:
            rank = getattr(s, "data_rank", "")
            name = getattr(s, "hts_kor_isnm", "")
            mktcap = getattr(s, "stck_avls", "")
            price = getattr(s, "stck_prpr", "")
            print(f"  순위: {rank}, 종목명: {name}, 시가총액: {mktcap}, 현재가: {price}")
    def display_top_market_cap_stocks_empty(self):
        print("\n시가총액 상위 종목이 없습니다.")
    def display_top_market_cap_stocks_failure(self, msg: str):
        print(f"\n실패: 시가총액 상위 종목 조회. 사유: {msg}")

    # 시총 TOP10 현재가
    def display_top10_market_cap_prices_success(self, items):
        print("\n성공: 시가총액 1~10위 종목 현재가:")
        for s in items:
            # ResMarketCapStockItem 기준
            rank = getattr(s, "rank", "")
            name = getattr(s, "name", "")
            code = getattr(s, "code", "")
            price = getattr(s, "current_price", "")
            print(f"  순위: {rank}, 종목명: {name}, 종목코드: {code}, 현재가: {price}원")
    def display_top10_market_cap_prices_empty(self):
        print("\n성공: 시가총액 1~10위 종목 현재가 (조회된 종목 없음)")
    def display_top10_market_cap_prices_failure(self, msg: str):
        print(f"\n실패: 시가총액 1~10위 종목 현재가 조회. 사유: {msg}")

    # 상한가 (당일)
    def display_upper_limit_stocks_success(self, items: list[dict]):
        print("\n--- 상한가 종목 목록 ---")
        for s in items:
            print(f"  {s['name']} ({s['code']}): {s['price']}원 (등락률: +{s['change_rate']}%)")
    def display_upper_limit_stocks_empty(self):
        print("\n현재 상한가에 도달한 종목이 없습니다.")
    def display_upper_limit_stocks_failure(self, msg: str):
        print(f"\n실패: 상한가 종목 조회. 사유: {msg}")

    def display_no_stocks_for_strategy(self):
        """전략 실행을 위한 종목이 없음을 알립니다."""
        self._print_common_header()
        print("전략을 실행할 종목이 없습니다.")

    def display_strategy_results(self, strategy_name: str, results: dict):
        """전략 실행 결과를 요약하여 표시합니다."""
        self._print_common_header()
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
        self._print_common_header()
        print(f"\n[오류] 전략 실행 중 문제 발생: {message}")

    def display_invalid_menu_choice(self):
        """잘못된 메뉴 선택 메시지를 표시합니다."""
        self._print_common_header()
        print("잘못된 메뉴 선택입니다. 다시 시도해주세요.")

    def display_warning_strategy_market_closed(self):
        """시장이 닫혔을 때 전략 실행 경고 메시지를 표시합니다."""
        self._print_common_header()
        print("⚠️ 시장이 폐장 상태이므로 전략을 실행할 수 없습니다.")

    def display_follow_through_stocks(self, stocks: list):
        """Follow Through 종목 목록을 표시합니다."""
        self._print_common_header()
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
        self._print_common_header()
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
        self._print_common_header()
        print("✔️ 후보 종목:")
        if stocks:
            for item in stocks:
                print(f" - {item.get('name', 'N/A')}({item.get('code', 'N/A')}) - 등락률 ({item.get('prdy_ctrt', 'N/A')})")
        else:
            print("   없음")

    def display_gapup_pullback_rejected_stocks(self, stocks: list):
        """GapUpPullback 전략의 제외 종목 목록을 표시합니다."""
        self._print_common_header()
        print("❌ 제외 종목:")
        if stocks:
            for item in stocks:
                print(f" - {item.get('name', 'N/A')}({item.get('code', 'N/A')})")
        else:
            print("   없음")

    def display_invalid_input_warning(self, message: str):
        """사용자 입력 경고 메시지를 표시합니다."""
        self._print_common_header()
        print(f"WARNING: {message}")

    def display_exit_message(self):
        """종료 메시지를 표시합니다."""
        self._print_common_header()
        print("애플리케이션을 종료합니다.")

    def display_token_invalidated_message(self):
        """토큰 무효화 메시지를 표시합니다."""
        self._print_common_header()
        print("토큰이 무효화되었습니다. 다음 요청 시 새 토큰이 발급됩니다.")

    def display_account_balance_failure(self, msg):
        """계좌 잔고 조회 실패 메시지를 표시합니다."""
        self._print_common_header()
        print(f"계좌 잔고 조회에 실패했습니다.{msg}")

    def display_stock_code_not_found(self, stock_name: str):
        """종목 코드를 찾을 수 없을 때 메시지를 표시합니다."""
        self._print_common_header()
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
        self._print_common_header()
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
        self._print_common_header()
        print("\n--- 거래 환경 선택 ---")
        print("1. 모의투자")
        print("2. 실전투자")
        print("-----------------------")
        # asyncio.to_thread를 사용하여 동기 input() 함수를 비동기 환경에서 실행
        choice = (await asyncio.to_thread(input, "환경을 선택하세요 (숫자 입력): ")).strip()
        return choice

    def display_warning_paper_trading_not_supported(self, msg):
        self._print_common_header()
        print(f"\"{msg}\"는 실전 전용 기능입니다.")

    def display_invalid_environment_choice(self, msg):
        self._print_common_header()
        print(f"\"{msg}\" 잘못된 환경 선택입니다.")

    def display_current_stock_price(self, view: dict):
        code   = str(view.get("code", "N/A"))
        price  = str(view.get("price", "N/A"))
        change = str(view.get("change", "N/A"))
        rate   = str(view.get("rate", "N/A"))
        time_  = str(view.get("time", "N/A"))
        open_  = str(view.get("open", "N/A"))
        high   = str(view.get("high", "N/A"))
        low    = str(view.get("low", "N/A"))
        prev   = str(view.get("prev_close", "N/A"))
        vol    = str(view.get("volume", "N/A"))

        print(f"\n--- {code} 현재가 ---")
        print(f"  현재가: {price}")
        print(f"  전일대비: {change} ({rate}%)")
        print(f"  체결시각: {time_}")
        print("-" * 36)
        print(f"  시가: {open_} / 고가: {high} / 저가: {low} / 전일종가: {prev}")
        print(f"  거래량: {vol}")
        print("-" * 36)

    def display_current_stock_price_error(self, code: str, msg: str):
        print(f"\n실패: {code} 현재가 조회. ({msg})")

    # 테스트/호환용 래퍼 (기존 TC가 handle_*를 스파이할 수 있게)
    def handle_get_current_stock_price(self, view: dict):
        self.display_current_stock_price(view)

    def display_current_upper_limit_stocks(self, stocks: list):
        """현재 상한가 종목 리스트를 표시합니다."""
        self._print_common_header()
        print("\n--- 현재 상한가 종목 ---")
        print(f"현재 상한가 종목 조회 성공. 총 {len(stocks)}개")
        self.logger.info(f"현재 상한가 종목 조회 성공. 총 {len(stocks)}개")
        self.logger.info("\n--- 현재 상한가 종목 ---")
        for s in stocks:
            # s가 dataclass(ResBasicStockInfo)거나 dict 둘 다 지원
            code = getattr(s, "code", None) or (s.get("code") if isinstance(s, dict) else "N/A")
            name = getattr(s, "name", None) or (s.get("name") if isinstance(s, dict) else "N/A")
            price = getattr(s, "current_price", None) or (s.get("current_price") or s.get("price") if isinstance(s, dict) else "N/A")
            prdy_ctrt = getattr(s, "prdy_ctrt", None) or (s.get("prdy_ctrt") if isinstance(s, dict) else "N/A")

            print(f"  {name} ({code}): {price}원 (등락률: +{prdy_ctrt}%)")
            self.logger.info(f"  {name} ({code}): {price}원 (등락률: +{prdy_ctrt}%)")

    def display_no_current_upper_limit_stocks(self):
        """현재 상한가 종목이 없을 때 메시지."""
        self._print_common_header()
        print("현재 상한가에 해당하는 종목이 없습니다.")

    def display_top_stocks_ranking(self, title: str, items: list[ResFluctuation]) -> None:
        """상위 랭킹(상승/하락/거래량) 공통 표 출력."""
        self._print_common_header()
        print(f"\n--- {title} 상위 종목 조회 ---")

        # items: dict 리스트 또는 ResFluctuation 리스트 모두 허용
        def _get(d, key, default="N/A"):
            if isinstance(d, dict):
                return d.get(key, default)
            # ResFluctuation 등 dataclass 지원
            return getattr(d, key, default)

        # 필요 시 dict(output=...) 포맷이 넘어오면 추출
        if isinstance(items, dict) and "output" in items:
            items = items["output"]

        if not items:
            print("표시할 종목이 없습니다.")
            return

        print("\n성공: {0} 상위 30개 종목".format(title))
        print("-" * 90)
        print(f"{'순위':<4} {'종목명':<30} {'현재가':>10} {'등락률(%)':>10} {'거래량':>15}")
        print("-" * 90)

        for item in items[:30]:
            rank   = _get(item, "data_rank")
            name   = _get(item, "hts_kor_isnm")
            price  = _get(item, "stck_prpr")
            rate   = _get(item, "prdy_ctrt")
            volume = _get(item, "acml_vol")

            rank_s   = str(rank)   if rank   not in (None, "") else "N/A"
            name_s   = str(name)   if name   not in (None, "") else "N/A"
            price_s  = str(price)  if price  not in (None, "") else "N/A"
            rate_s   = str(rate)   if rate   not in (None, "") else "N/A"
            volume_s = str(volume) if volume not in (None, "") else "N/A"
            print(f"{rank_s:<4} {name_s:<30} {price_s:>10} {rate_s:>10} {volume_s:>15}")

        print("-" * 90)

    def display_top_stocks_ranking_error(self, title: str, msg: str) -> None:
        self._print_common_header()
        print(f"\n실패: {title} 상위 종목 조회. ({msg})")

    def display_stock_news(self, stock_code: str, news_list: list) -> None:
        self._print_common_header()
        print(f"\n--- {stock_code} 종목 뉴스 조회 ---")

        # dict(output=...) 포맷 대응
        if isinstance(news_list, dict) and "output" in news_list:
            news_list = news_list["output"]

        if not news_list:
            print(f"\n{stock_code}에 대한 뉴스가 없습니다.")
            return

        print(f"\n성공: {stock_code} 최신 뉴스 (최대 5건)")
        print("-" * 70)
        for item in news_list[:5]:
            news_date = item.get('news_dt', '') if isinstance(item, dict) else getattr(item, 'news_dt', '')
            news_time = item.get('news_tm', '') if isinstance(item, dict) else getattr(item, 'news_tm', '')
            title     = item.get('news_tl', 'N/A') if isinstance(item, dict) else getattr(item, 'news_tl', 'N/A')
            print(f"[{news_date} {news_time}] {title}")
        print("-" * 70)

    def display_stock_news_error(self, stock_code: str, msg: str) -> None:
        self._print_common_header()
        print(f"\n실패: {stock_code} 종목 뉴스 조회. ({msg})")

    # ===== 호가 =====
    def display_asking_price(self, view: dict):
        code = view.get("code", "N/A")
        rows = view.get("rows", [])
        print(f"\n--- {code} 실시간 호가 ---")
        print("-" * 40)
        print(f"{'레벨':>4s} | {'매도잔량':>10s} | {'호가':>10s} | {'매수잔량':>10s} | {'호가':>10s}")
        print("-" * 40)
        for r in rows:
            lv   = str(r.get("level", ""))
            askr = str(r.get("ask_rem", "N/A"))
            askp = str(r.get("ask_price", "N/A"))
            bidr = str(r.get("bid_rem", "N/A"))
            bidp = str(r.get("bid_price", "N/A"))
            print(f"{lv:>4s} | {askr:>10s} | {askp:>10s} | {bidr:>10s} | {bidp:>10s}")
        print("-" * 40)

    def display_asking_price_error(self, code: str, msg: str):
        print(f"\n실패: {code} 호가 정보 조회. ({msg})")

    # ===== 시간대별 체결가 =====
    def display_time_concluded_prices(self, view: dict):
        code = view.get("code", "N/A")
        rows = view.get("rows", [])
        print(f"\n--- {code} 시간대별 체결 정보 (최근 {len(rows)}건) ---")
        print("-" * 56)
        print(f"{'체결시각':>10s} | {'체결가':>12s} | {'전일대비':>10s} | {'체결량':>10s}")
        print("-" * 56)
        for r in rows:
            t = str(r.get("time", "N/A"))
            p = str(r.get("price", "N/A"))
            c = str(r.get("change", "N/A"))
            v = str(r.get("volume", "N/A"))
            print(f"{t:>10s} | {p:>12s} | {c:>10s} | {v:>10s}")
        print("-" * 56)

    def display_time_concluded_error(self, code: str, msg: str):
        print(f"\n실패: {code} 시간대별 체결가 조회. ({msg})")

    def display_etf_info(self, etf_code: str, etf_info: dict) -> None:
        self._print_common_header()
        print(f"\n--- {etf_code} ETF 정보 조회 ---")

        # dict(output=...) 포맷 대응
        if isinstance(etf_info, dict) and "output" in etf_info:
            etf_info = etf_info["output"]

        name        = etf_info.get('etf_rprs_bstp_kor_isnm', 'N/A')
        price       = etf_info.get('stck_prpr', 'N/A')
        nav         = etf_info.get('nav', 'N/A')
        market_cap  = etf_info.get('stck_llam', 'N/A')

        print(f"\n성공: {name} ({etf_code})")
        print("-" * 40)
        print(f"  현재가: {price} 원")
        print(f"  NAV: {nav}")
        print(f"  시가총액: {market_cap} 원")
        print("-" * 40)

    def display_etf_info_error(self, etf_code: str, msg: str) -> None:
        self._print_common_header()
        print(f"\n실패: {etf_code} ETF 정보 조회. ({msg})")

    def display_ohlcv(self, stock_code: str, rows: list[dict]):
        """OHLCV 표 출력 (최근 10개 미리보기)."""
        self._print_common_header()
        print(f"\n--- {stock_code} OHLCV ---")

        if not rows:
            print("데이터가 없습니다.")
            return

        preview = rows[-30:]
        print("-" * 78)
        print(f"{'DATE':<10} | {'OPEN':>10} | {'HIGH':>10} | {'LOW':>10} | {'CLOSE':>10} | {'VOLUME':>12}")
        print("-" * 78)
        for r in preview:
            print(
                f"{str(r.get('date','')):<10} | "
                f"{str(r.get('open','')):>10} | "
                f"{str(r.get('high','')):>10} | "
                f"{str(r.get('low','')):>10} | "
                f"{str(r.get('close','')):>10} | "
                f"{str(r.get('volume','')):>12}"
            )
        print("-" * 78)

    def display_ohlcv_error(self, stock_code: str, message: str):
        """OHLCV 조회 실패 출력."""
        self._print_common_header()
        print(f"\n실패: {stock_code} OHLCV 조회. ({message})")

    def display_intraday_minutes(self, stock_code: str, rows, title: str = "분봉"):
        """
        출력 방식:
          - 시간 오름차순(가장 과거 → 최신)으로 정렬
          - 전체 개수가 20개 초과 시: 앞 10개 출력 → 생략 표시 → 뒤 10개 출력
          - 번호는 전체 인덱스(1-based) 기준으로 표기
        rows는 list 또는 {"output2": [...]} dict 모두 수용
        """
        self._print_common_header()
        print(f"\n--- {title} 조회 결과: {stock_code} ---")

        # rows 정규화: dict로 온 경우 output2/rows/data 키에서 추출
        if isinstance(rows, dict):
            rows = rows.get("output2") or rows.get("rows") or rows.get("data") or []
        if not isinstance(rows, list):
            rows = []

        if not rows:
            print("데이터가 없습니다.")
            return

        tm = getattr(self, "time_manager", None)

        def _key(r):
            if isinstance(r, dict):
                d = str(r.get("stck_bsop_date") or r.get("bsop_date") or r.get("date") or "")
                t_raw = r.get("stck_cntg_hour") or r.get("cntg_hour") or r.get("time") or ""
                t = tm.to_hhmmss(t_raw) if tm else str(t_raw)
                return (d, t)
            return ("", "000000")

        # 시간 오름차순 정렬
        sorted_rows = sorted(rows, key=_key)
        n = len(sorted_rows)
        width = len(str(n))
        top_n = 10
        bottom_n = 10

        def _print_row(idx: int, r):
            if not isinstance(r, dict):
                print(f"{idx:>{width}}. {r}")
                return
            dt   = r.get("stck_bsop_date") or r.get("bsop_date") or r.get("date") or ""
            time = r.get("stck_cntg_hour") or r.get("cntg_hour") or r.get("time") or ""
            if tm:
                time = tm.to_hhmmss(time)
            o    = r.get("stck_oprc") or r.get("oprc") or r.get("open")
            h    = r.get("stck_hgpr") or r.get("hgpr") or r.get("high")
            l    = r.get("stck_lwpr") or r.get("lwpr") or r.get("low")
            c    = r.get("stck_prpr") or r.get("prpr") or r.get("close") or r.get("price")
            vol  = r.get("cntg_vol") or r.get("acml_vol") or r.get("volume")
            # 포맷팅
            o = "-" if o is None else o
            h = "-" if h is None else h
            l = "-" if l is None else l
            c = "-" if c is None else c
            vol = "-" if vol is None else vol
            dt_time = f"{dt} {time}".strip()
            print(f"{idx:>{width}}. {dt_time} | O:{o} H:{h} L:{l} C:{c} V:{vol}")

        if n <= top_n + bottom_n:
            for i, r in enumerate(sorted_rows, 1):
                _print_row(i, r)
        else:
            # 앞 10개
            for i, r in enumerate(sorted_rows[:top_n], 1):
                _print_row(i, r)
            omitted = n - (top_n + bottom_n)
            print(f"... ({omitted}개 생략) ...")
            # 뒤 10개 (전체 인덱스로 번호 표시)
            start_idx = n - bottom_n + 1
            for offset, r in enumerate(sorted_rows[-bottom_n:], 0):
                _print_row(start_idx + offset, r)

    def display_intraday_error(self, stock_code: str, message: str):
        self._print_common_header()
        print(f"\n❌ 분봉 조회 실패 - {stock_code}: {message}")

    def display_intraday_minutes_full_day(self, stock_code: str, rows: list, date_ymd: str, session: str):
        """하루치 분봉(세션 메타 포함) 출력 헬퍼. 내부적으로 display_intraday_minutes 재사용."""
        session_label = "08:00~20:00" if str(session).upper() == "EXTENDED" else "09:00~15:30"
        title = f"하루 분봉 ({date_ymd} {session_label})"
        self.display_intraday_minutes(stock_code, rows, title=title)

def display_virtual_trade_summary(self, summary: dict):
        """가상 매매 요약 정보를 출력합니다."""
        self._print_common_header()
        print("\n=== 📊 가상 매매(Backtest/Paper) 요약 ===")
        
        total = summary.get('total_trades', 0)
        wins = summary.get('win_trades', 0)
        win_rate = summary.get('win_rate', 0.0)
        avg_ret = summary.get('avg_return', 0.0)
        
        print(f"총 거래 횟수 : {total}회")
        print(f"승리 횟수    : {wins}회")
        print(f"승률         : {win_rate:.2f}%")
        print(f"평균 수익률  : {avg_ret:.2f}%")
        print("------------------------------------------")

def display_virtual_trade_history(self, trades: list):
    """가상 매매 상세 기록을 테이블 형태로 출력합니다."""
    if not trades:
        print("  (거래 기록이 없습니다)")
        return

    print(f"{'전략':<12} | {'종목':<8} | {'매수일':<10} | {'매수가':>8} | {'매도일':<10} | {'매도가':>8} | {'수익률':>7} | {'상태':<5}")
    print("-" * 90)

    for t in trades:
        # 딕셔너리 키는 VirtualTradeManager 구현에 따라 다를 수 있음
        strategy = t.get('strategy', 'N/A')[:10]
        code = t.get('code', 'N/A')
        buy_date = str(t.get('buy_date', ''))[:10]  # 날짜만 표시
        buy_price = int(t.get('buy_price', 0))
        
        sell_date = str(t.get('sell_date', ''))[:10] if t.get('sell_date') else '-'
        sell_price = int(t.get('sell_price', 0)) if t.get('sell_price') else 0
        sell_price_str = f"{sell_price:>8,}" if sell_price > 0 else f"{'-':>8}"
        
        ror = t.get('return_rate', 0.0)
        status = t.get('status', 'HOLD')
        
        # 수익률 포맷팅
        ror_str = f"{ror:+.2f}%"
        
        print(f"{strategy:<12} | {code:<8} | {buy_date:<10} | {buy_price:>8,} | {sell_date:<10} | {sell_price_str} | {ror_str:>7} | {status:<5}")
    
    print("-" * 90)
