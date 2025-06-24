# app/data_handlers.py
import asyncio


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

    async def handle_get_top_market_cap_stocks(self, market_code, count: int = None):
        """시가총액 상위 종목 조회 요청 및 결과 출력 (전체 목록)."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")
        top_market_cap_stocks = await self.trading_service.get_top_market_cap_stocks(market_code, count)

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
            print(f"  전일대비: {display_change_val}원")
            print(f"  전일대비율: {change_rate_str}%")

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

    async def handle_upper_limit_stocks(self, market_code: str = "0000", limit: int = 500):
        """
        시가총액 상위 종목 중 상한가에 도달한 종목을 조회하여 출력합니다.
        이 기능은 모의투자를 지원하지 않으며, 시장 개장 시에만 조회 가능합니다. (스냅샷 기준)
        :param market_code: 시장 분류 코드 (예: "0000" 코스피)
        :param limit: 조회할 상위 종목의 최대 개수
        """
        print(f"\n--- 시가총액 상위 {limit}개 종목 중 상한가 종목 조회 ---")
        # --- 추가된 로그 ---
        self.logger.info(f"Service - 시가총액 상위 {limit}개 종목 중 상한가 종목 조회 요청")
        # -------------------

        # 시장 개장 및 모의투자 여부 확인
        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.")
            print("WARNING: 시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.\n")
            return None  # None 반환하여 상위 호출자에게 실패 알림

        if self.trading_service._env.is_paper_trading:  # trading_service 내부의 env 확인
            self.logger.warning("Service - 상한가 종목 조회는 모의투자를 지원하지 않습니다.")
            print("WARNING: 모의투자 환경에서는 상한가 종목 조회를 지원하지 않습니다.\n")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}  # 오류 딕셔너리 반환

        upper_limit_stocks_found = []
        try:
            # 1. 시가총액 상위 종목 목록 조회 (TradingService 위임)
            top_stocks_response = await self.trading_service.get_top_market_cap_stocks(market_code)

            if not top_stocks_response or top_stocks_response.get('rt_cd') != '0' or not top_stocks_response.get(
                    'output'):
                self.logger.error(f"시가총액 상위 종목 목록 조회 실패: {top_stocks_response}")
                print(f"실패: 시가총액 상위 종목 목록을 가져올 수 없습니다. {top_stocks_response.get('msg1', '')}\n")
                return None

            top_stocks_list = top_stocks_response.get('output', [])
            if not top_stocks_list:
                self.logger.info("조회된 시가총액 상위 종목이 없습니다.")
                print("조회된 시가총액 상위 종목이 없습니다.\n")
                return None

            # 상위 limit 개수까지만 처리
            top_stocks_to_check = top_stocks_list[:limit]

            print(f"조회 대상 종목: 총 {len(top_stocks_to_check)}개")
            current_checked_count = 0

            # 2. 각 종목의 현재가 조회 및 상한가 여부 판단
            for stock_info in top_stocks_to_check:
                stock_code = stock_info.get('mksc_shrn_iscd')
                stock_name = stock_info.get('hts_kor_isnm')

                if stock_code:
                    current_price_response = await self.trading_service.get_current_stock_price(stock_code)
                    current_checked_count += 1
                    print(f"\r조회 중... {current_checked_count}/{len(top_stocks_to_check)}", end="")

                    if current_price_response and current_price_response.get('rt_cd') == '0':
                        output_data = current_price_response.get('output', {})
                        prdy_vrss_sign = output_data.get('prdy_vrss_sign', 'N/A')  # 전일대비 부호
                        stck_prpr = output_data.get('stck_prpr', 'N/A')  # 현재가
                        prdy_ctrt = output_data.get('prdy_ctrt', 'N/A')  # 전일대비율

                        # 상한가 부호는 '1' (상한)
                        if prdy_vrss_sign == '1':
                            upper_limit_stocks_found.append({
                                "code": stock_code,
                                "name": stock_name,
                                "price": stck_prpr,
                                "change_rate": prdy_ctrt  # 상한가에서는 등락률도 중요
                            })
                            self.logger.info(
                                f"상한가 종목 발견: {stock_name} ({stock_code}), 현재가: {stck_prpr}, 등락률: {prdy_ctrt}%")
                    else:
                        self.logger.warning(f"종목 {stock_name} ({stock_code}) 현재가 조회 실패: {current_price_response}")
                else:
                    self.logger.warning(f"유효한 종목코드를 찾을 수 없습니다: {stock_info}")

            print("\r" + " " * 80 + "\r", end="")  # 진행 메시지 지우기

            # 3. 결과 출력
            if upper_limit_stocks_found:
                print("\n--- 상한가 종목 목록 ---")
                for stock in upper_limit_stocks_found:
                    print(f"  {stock['name']} ({stock['code']}): {stock['price']}원 (등락률: +{stock['change_rate']}%)\n")
                self.logger.info(f"총 {len(upper_limit_stocks_found)}개의 상한가 종목 발견.")
                return True  # 성공적으로 상한가 종목을 찾았거나 목록을 출력했음을 알림
            else:
                print("\n현재 상한가에 도달한 종목이 없습니다.\n")
                self.logger.info("현재 상한가에 도달한 종목이 없습니다.")
                return False  # 상한가 종목을 찾지 못했음을 알림

        except Exception as e:
            self.logger.error(f"상한가 종목 조회 중 예기치 않은 오류 발생: {e}")
            print(f"실패: 상한가 종목 조회 중 오류 발생. {e}\n")
            return None  # 예외 발생 시 None 반환

