# app/data_handlers.py
import asyncio  # input()을 비동기적으로 처리하기 위해 필요


class DataHandlers:
    """
    주식 현재가, 계좌 잔고, 시가총액 조회 등 데이터 조회 관련 핸들러를 관리하는 클래스입니다.
    TradingService, Logger, TimeManager 인스턴스를 주입받아 사용합니다.
    """

    def __init__(self, trading_service, logger, time_manager):
        self.trading_service = trading_service
        self.logger = logger
        self.time_manager = time_manager

    def _get_sign_from_code(self, sign_code):
        """API 응답의 부호 코드(1,2,3,4,5)를 실제 부호 문자열로 변환합니다."""
        if sign_code == '1' or sign_code == '2':  # 1:상한, 2:상승
            return "+"
        elif sign_code == '4' or sign_code == '5':  # 4:하한, 5:하락
            return "-"
        else:  # 3:보합 (또는 기타)
            return ""

    async def handle_get_current_stock_price(self, stock_code):
        """주식 현재가 조회 요청 및 결과 출력."""
        print(f"\n--- {stock_code} 현재가 조회 ---")
        current_price_result = await self.trading_service.get_current_stock_price(stock_code)
        if current_price_result and current_price_result.get('rt_cd') == '0':
            print(f"\n{stock_code} 현재가: {current_price_result}")
            self.logger.info(f"{stock_code} 현재가 조회 성공: {current_price_result}")
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result}")

    async def handle_get_account_balance(self):
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")
        account_balance = await self.trading_service.get_account_balance()
        if account_balance and account_balance.get('rt_cd') == '0':
            print(f"\n계좌 잔고: {account_balance}")
            self.logger.info(f"계좌 잔고 조회 성공: {account_balance}")
        else:
            print(f"\n계좌 잔고 조회 실패.")
            self.logger.error(f"계좌 잔고 조회 실패: {account_balance}")

    async def handle_get_top_market_cap_stocks(self, market_code):
        """시가총액 상위 종목 조회 요청 및 결과 출력 (전체 목록)."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")
        top_market_cap_stocks = await self.trading_service.get_top_market_cap_stocks(market_code)

        if top_market_cap_stocks and top_market_cap_stocks.get('rt_cd') == '0':
            print(f"성공: 시가총액 상위 종목 목록:")
            for stock_info in top_market_cap_stocks.get('output', []):
                print(f"  순위: {stock_info.get('data_rank', '')}, "
                      f"종목명: {stock_info.get('hts_kor_isnm', '')}, "
                      f"시가총액: {stock_info.get('stck_avls', '')}, "
                      f"현재가: {stock_info.get('stck_prpr', '')}")
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}), 결과: {top_market_cap_stocks}")
        else:
            print(f"실패: 시가총액 상위 종목 조회.")
            self.logger.error(f"실패: 시가총액 상위 종목 조회: {top_market_cap_stocks}")

    async def handle_get_top_10_market_cap_stocks_with_prices(self):
        """
        시가총액 1~10위 종목의 현재가를 조회하고 출력하는 핸들러.
        성공 시 True, 실패 시 False 반환 (앱 종료 여부 판단용).
        """
        print("\n--- 시가총액 1~10위 종목 현재가 조회 시도 ---")
        top_10_with_prices = await self.trading_service.get_top_10_market_cap_stocks_with_prices()

        if top_10_with_prices:
            print("\n성공: 시가총액 1~10위 종목 현재가:")
            for stock in top_10_with_prices:
                print(
                    f"  순위: {stock['rank']}, 종목명: {stock['name']}, 종목코드: {stock['code']}, 현재가: {stock['current_price']}원")
            self.logger.info(f"시가총액 1~10위 종목 현재가 조회 성공: {top_10_with_prices}")
            return True
        else:
            print("\n실패: 시가총액 1~10위 종목 현재가 조회.")
            self.logger.error("시가총액 1~10위 종목 현재가 조회 실패.")
            return False

    async def handle_display_stock_change_rate(self, stock_code):
        """
        주식 전일대비 등락률을 조회하고 콘솔에 출력합니다.
        TradingService를 통해 현재 주가 정보를 가져와 등락률을 표시합니다.
        :param stock_code: 조회할 주식 종목코드 (예: "005930")
        """
        print(f"\n--- {stock_code} 전일대비 등락률 조회 ---")

        current_price_result = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result and current_price_result.get('rt_cd') == '0':
            output_data = current_price_result.get('output', {})
            current_price = output_data.get('stck_prpr', 'N/A')
            change_val_str = output_data.get('prdy_vrss', 'N/A')  # 문자열로 가져옴
            change_sign_code = output_data.get('prdy_vrss_sign', 'N/A')  # 부호 코드
            change_rate_str = output_data.get('prdy_ctrt', 'N/A')  # 문자열로 가져옴

            # 부호 코드에 따른 실제 부호 문자열 결정
            actual_change_sign = self._get_sign_from_code(change_sign_code)

            # 0원일 경우 부호 없이 0으로 표시
            display_change_val = change_val_str
            if change_val_str != 'N/A':
                try:
                    change_val_float = float(change_val_str)
                    if change_val_float == 0:
                        display_change_val = "0"
                        actual_change_sign = ""  # 0일 때는 부호 없음
                    elif actual_change_sign:  # 부호가 있고 0이 아니면 부호 붙임
                        display_change_val = f"{actual_change_sign}{change_val_str}"
                except ValueError:
                    pass  # 숫자로 변환 불가능하면 N/A 그대로 사용

            print(f"\n성공: {stock_code} ({current_price}원)")
            print(f"  전일대비: {display_change_val}원")  # 수정된 변수 사용
            print(f"  전일대비율: {change_rate_str}%")  # change_rate_str 사용

            self.logger.info(
                f"{stock_code} 전일대비 등락률 조회 성공: 현재가={current_price}, "
                f"전일대비={display_change_val}, 등락률={change_rate_str}%"
            )
        else:
            print(f"\n실패: {stock_code} 전일대비 등락률 조회.")
            self.logger.error(f"{stock_code} 전일대비 등락률 조회 실패: {current_price_result}")

    async def handle_display_stock_vs_open_price(self, stock_code):
        """
        주식 시가대비 조회 및 결과 출력.
        TradingService를 통해 현재 주가 정보를 가져와 시가 대비를 표시합니다.
        :param stock_code: 조회할 주식 종목코드 (예: "005930")
        """
        print(f"\n--- {stock_code} 시가대비 조회 ---")

        current_price_result = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result and current_price_result.get('rt_cd') == '0':
            output_data = current_price_result.get('output', {})
            current_price_str = output_data.get('stck_prpr', 'N/A')
            open_price_str = output_data.get('stck_oprc', 'N/A')
            vs_open_sign_code = output_data.get('oprc_vrss_prpr_sign', 'N/A')  # 부호 코드

            current_price_float = float(current_price_str) if current_price_str != 'N/A' else None
            open_price_float = float(open_price_str) if open_price_str != 'N/A' else None

            # 시가대비 등락률 퍼센트 계산
            percentage_change_vs_open_formatted = "N/A"
            if open_price_float is not None and current_price_float is not None and open_price_float != 0:
                percentage_change_vs_open = ((current_price_float - open_price_float) / open_price_float) * 100

                # 퍼센트 부호 적용 및 0.00% 처리
                if percentage_change_vs_open > 0:
                    percentage_change_vs_open_formatted = f"+{percentage_change_vs_open:.2f}%"
                elif percentage_change_vs_open < 0:
                    percentage_change_vs_open_formatted = f"{percentage_change_vs_open:.2f}%"
                else:
                    percentage_change_vs_open_formatted = "0.00%"
            else:
                percentage_change_vs_open_formatted = "N/A"

            # 시가 대비 금액 계산 및 부호 적용, 0원 처리
            display_vs_open_price_formatted = "N/A"
            if open_price_float is not None and current_price_float is not None:
                calculated_vs_open_price = current_price_float - open_price_float

                # 금액 부호 적용 및 0원 처리
                if calculated_vs_open_price > 0:
                    display_vs_open_price_formatted = f"+{calculated_vs_open_price:.0f}"
                elif calculated_vs_open_price < 0:
                    display_vs_open_price_formatted = f"{calculated_vs_open_price:.0f}"
                else:
                    display_vs_open_price_formatted = "0"

            print(f"\n성공: {stock_code}")
            print(f"  현재가: {current_price_str}원")
            print(f"  시가: {open_price_str}원")
            print(f"  시가대비 등락률: {display_vs_open_price_formatted}원 ({percentage_change_vs_open_formatted})")

            self.logger.info(
                f"{stock_code} 시가대비 조회 성공: 현재가={current_price_str}, 시가={open_price_str}, "
                f"시가대비={display_vs_open_price_formatted}원 ({percentage_change_vs_open_formatted})"
            )
        else:
            print(f"\n실패: {stock_code} 시가대비 조회.")
            self.logger.error(f"{stock_code} 시가대비 조회 실패: {current_price_result}")
