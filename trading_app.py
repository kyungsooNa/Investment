# trading_app.py
import os
from core.config_loader import load_config
from api.env import KoreaInvestEnv
from api.client import KoreaInvestAPI
from services.trading_service import TradingService
from core.time_manager import TimeManager
from core.logger import Logger
import asyncio  # 비동기 sleep을 위해 필요


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
        print("6. 실시간 주식 체결가/호가 구독 (삼성전자)")
        print("7. 주식 전일대비 등락률 조회 (삼성전자)")
        print("8. 주식 시가대비 조회 (삼성전자)")  # <--- 새로운 메뉴 옵션 추가
        print("0. 종료")
        print("-----------------------------------")

    async def _execute_action(self, choice):
        """사용자 선택에 따라 해당 작업을 실행하고 결과를 콘솔에 출력합니다."""
        running_status = True

        if choice == '1':
            await self._handle_get_current_stock_price("005930")
        elif choice == '2':
            await self._handle_get_account_balance()
        elif choice == '3':
            if not self.time_manager.is_market_open():
                print("WARNING: 시장이 닫혀 있어 주문을 제출할 수 없습니다.")
            else:
                await self._handle_place_buy_order("005930", "58500", "1", "00")
        elif choice == '4':
            await self._get_top_market_cap_stocks("0000")
        elif choice == '5':
            if await self._handle_get_top_10_market_cap_stocks_with_prices():
                running_status = False
        elif choice == '6':
            await self._handle_realtime_price_quote_stream("005930")
        elif choice == '7':
            await self._handle_display_stock_change_rate("005930")
        elif choice == '8':  # <--- 새로운 핸들러 호출
            await self._handle_display_stock_vs_open_price("005930")
        elif choice == '0':
            print("애플리케이션을 종료합니다.")
            running_status = False
        else:
            print("유효하지 않은 선택입니다. 다시 시도해주세요.")

        return running_status

    async def _handle_get_current_stock_price(self, stock_code):
        """주식 현재가 조회 요청 및 결과 출력."""
        print(f"\n--- {stock_code} 현재가 조회 ---")
        current_price_result = await self.trading_service.get_current_stock_price(stock_code)
        if current_price_result and current_price_result.get('rt_cd') == '0':
            print(f"\n{stock_code} 현재가: {current_price_result}")
            self.logger.info(f"{stock_code} 현재가 조회 성공: {current_price_result}")
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result}")

    async def _handle_get_account_balance(self):
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")
        account_balance = await self.trading_service.get_account_balance()
        if account_balance and account_balance.get('rt_cd') == '0':
            print(f"\n계좌 잔고: {account_balance}")
            self.logger.info(f"계좌 잔고 조회 성공: {account_balance}")
        else:
            print(f"\n계좌 잔고 조회 실패.")
            self.logger.error(f"계좌 잔고 조회 실패: {account_balance}")

    async def _handle_place_buy_order(self, stock_code, price, qty, order_dvsn):
        """주식 매수 주문 요청 및 결과 출력."""
        print("\n--- 주식 매수 주문 시도 ---")
        buy_order_result = await self.trading_service.place_buy_order(
            stock_code, price, qty, order_dvsn
        )
        if buy_order_result and buy_order_result.get('rt_cd') == '0':
            print(f"주식 매수 주문 성공: {buy_order_result}")
            self.logger.info(f"주식 매수 주문 성공: 종목={stock_code}, 수량={qty}, 결과={buy_order_result}")
        else:
            print(f"주식 매수 주문 실패: {buy_order_result}")
            self.logger.error(f"주식 매수 주문 실패: 종목={stock_code}, 결과={buy_order_result}")

    async def _get_top_market_cap_stocks(self, market_code):
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

    async def _handle_get_top_10_market_cap_stocks_with_prices(self):
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

    async def _handle_realtime_price_quote_stream(self, stock_code):
        """
        실시간 주식 체결가/호가 스트림을 시작하고,
        사용자 입력이 있을 때까지 데이터를 수신합니다.
        """
        print(f"\n--- 실시간 주식 체결가/호가 구독 시작 ({stock_code}) ---")
        print("실시간 데이터를 수신 중입니다... (종료하려면 Enter를 누르세요)")

        # 콜백 함수 정의
        def realtime_data_display_callback(data):
            if isinstance(data, dict):  # data가 파싱된 딕셔너리라고 가정
                data_type = data.get('type')
                # tr_id = data.get('tr_id') # 사용되지 않아 제거
                output = data.get('data', {})

                if data_type == 'realtime_price':  # 주식 체결
                    current_price = output.get('STCK_PRPR', 'N/A')
                    acml_vol = output.get('ACML_VOL', 'N/A')
                    trade_time = output.get('STCK_CNTG_HOUR', 'N/A')
                    change_val = output.get('PRDY_VRSS', 'N/A')
                    change_sign = output.get('PRDY_VRSS_SIGN', 'N/A')
                    change_rate = output.get('PRDY_CTRT', 'N/A')

                    display_message = (
                        f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                        f"전일대비: {change_sign}{change_val} ({change_rate}%), 누적량: {acml_vol}"
                    )
                    print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'realtime_quote':  # 주식 호가
                    askp1 = output.get('매도호가1', 'N/A')
                    bidp1 = output.get('매수호가1', 'N/A')
                    trade_time = output.get('영업시간', 'N/A')
                    display_message = (
                        f"\r[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1: {askp1}, 매수1: {bidp1}{' ' * 20}"
                    )
                    print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
                elif data_type == 'signing_notice':  # 체결 통보
                    order_num = output.get('주문번호', 'N/A')
                    trade_qty = output.get('체결수량', 'N/A')
                    trade_price = output.get('체결단가', 'N/A')
                    trade_time = output.get('주식체결시간', 'N/A')
                    print(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")
                # else:
                #     self.logger.debug(f"처리되지 않은 실시간 메시지: {tr_id} - {data}")

        # 웹소켓 연결 및 구독 요청
        if await self.trading_service.connect_websocket(on_message_callback=realtime_data_display_callback):
            # 주식체결과 주식호가 동시 구독 예시
            await self.trading_service.subscribe_realtime_price(stock_code)
            await self.trading_service.subscribe_realtime_quote(stock_code)

            try:
                # 사용자 입력을 비동기적으로 기다림 (Enter 입력 시 계속 진행)
                await asyncio.to_thread(input)
                print("\n")

            except KeyboardInterrupt:
                print("\n사용자에 의해 실시간 구독이 중단됩니다.")
                self.logger.info("실시간 구독 중단 (KeyboardInterrupt).")
            finally:
                # 구독 해지 및 웹소켓 연결 종료
                await self.trading_service.unsubscribe_realtime_price(stock_code)
                await self.trading_service.unsubscribe_realtime_quote(stock_code)
                await self.trading_service.disconnect_websocket()
                print("실시간 주식 스트림을 종료했습니다.")
                self.logger.info(f"실시간 주식 스트림 종료: 종목={stock_code}")
        else:
            print("실시간 웹소켓 연결에 실패했습니다.")
            self.logger.error("실시간 웹소켓 연결 실패.")

    async def _handle_display_stock_change_rate(self, stock_code):
        """
        주식 전일대비 등락률을 조회하고 콘솔에 출력합니다.
        TradingService를 통해 현재 주가 정보를 가져와 등락률을 표시합니다.
        :param stock_code: 조회할 주식 종목코드 (예: "005930")
        """
        print(f"\n--- {stock_code} 전일대비 등락률 조회 ---")

        # TradingService를 통해 현재 주가 정보를 가져옵니다.
        # 이 호출은 api/quotations.py의 get_current_price를 사용하며,
        # 해당 API는 전일대비(prdy_vrss)와 전일대비율(prdy_ctrt) 정보를 포함합니다.
        current_price_result = await self.trading_service.get_current_stock_price(stock_code)

        # API 호출 결과 확인
        if current_price_result and current_price_result.get('rt_cd') == '0':
            # 응답 데이터의 'output' 필드에서 필요한 정보 추출
            output_data = current_price_result.get('output', {})
            current_price = output_data.get('stck_prpr', 'N/A')  # 주식 현재가
            change_val = output_data.get('prdy_vrss', 'N/A')  # 전일 대비 금액
            change_sign = output_data.get('prdy_vrss_sign', 'N/A')  # 전일 대비 부호 (1:상한, 2:상승, 3:보합, 4:하한, 5:하락)
            change_rate = output_data.get('prdy_ctrt', 'N/A')  # 전일 대비율

            # 콘솔에 등락률 정보 출력
            print(f"\n성공: {stock_code} ({current_price}원)")
            print(f"  전일대비: {change_sign}{change_val}원")
            print(f"  전일대비율: {change_rate}%")

            # 로깅 (operational.log 및 debug.log에 기록)
            self.logger.info(
                f"{stock_code} 전일대비 등락률 조회 성공: 현재가={current_price}, "
                f"전일대비={change_sign}{change_val}, 등락률={change_rate}%"
            )
        else:
            # 조회 실패 시 콘솔 및 로그에 오류 기록
            print(f"\n실패: {stock_code} 전일대비 등락률 조회.")
            self.logger.error(f"{stock_code} 전일대비 등락률 조회 실패: {current_price_result}")

    async def _handle_display_stock_vs_open_price(self, stock_code):
        """
        주식 시가대비 조회 및 결과 출력.
        TradingService를 통해 현재 주가 정보를 가져와 시가 대비를 표시합니다.
        :param stock_code: 조회할 주식 종목코드 (예: "005930")
        """
        print(f"\n--- {stock_code} 시가대비 조회 ---")

        # TradingService를 통해 현재 주가 정보를 가져옵니다.
        # 이 호출은 api/quotations.py의 get_current_price를 사용하며,
        # 해당 API는 시가(stck_oprc), 시가대비(oprc_vrss_prpr), 시가대비부호(oprc_vrss_prpr_sign) 정보를 포함합니다.
        current_price_result = await self.trading_service.get_current_stock_price(stock_code)

        # API 호출 결과 확인
        if current_price_result and current_price_result.get('rt_cd') == '0':
            # 응답 데이터의 'output' 필드에서 필요한 정보 추출
            output_data = current_price_result.get('output', {})
            current_price_str = output_data.get('stck_prpr', 'N/A')  # 주식 현재가 (문자열)
            open_price_str = output_data.get('stck_oprc', 'N/A')  # 주식 시가 (문자열)
            # vs_open_price_str = output_data.get('oprc_vrss_prpr', 'N/A') # 이 값은 이제 직접 계산하므로 필요 없음
            vs_open_sign = output_data.get('oprc_vrss_prpr_sign', 'N/A')  # 시가 대비 부호 (API 제공)

            # 숫자 값으로 변환 및 오류 처리
            current_price_float = float(current_price_str) if current_price_str != 'N/A' else None
            open_price_float = float(open_price_str) if open_price_str != 'N/A' else None

            # 시가대비 등락률 퍼센트 계산
            percentage_change_vs_open_formatted = "N/A"
            if open_price_float is not None and current_price_float is not None and open_price_float != 0:
                percentage_change_vs_open = ((current_price_float - open_price_float) / open_price_float) * 100

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
                calculated_vs_open_price = current_price_float - open_price_float  # <--- 직접 계산

                if calculated_vs_open_price > 0:
                    display_vs_open_price_formatted = f"+{calculated_vs_open_price:.0f}"
                elif calculated_vs_open_price < 0:
                    display_vs_open_price_formatted = f"{calculated_vs_open_price:.0f}"
                else:
                    display_vs_open_price_formatted = "0"

            # 콘솔에 등락률 정보 출력
            print(f"\n성공: {stock_code}")
            print(f"  현재가: {current_price_str}원")
            print(f"  시가: {open_price_str}원")
            # --- 출력 포맷 수정 ---
            print(f"  시가대비 등락률: {display_vs_open_price_formatted}원 ({percentage_change_vs_open_formatted})")

            # 로깅 (operational.log 및 debug.log에 기록)
            self.logger.info(
                f"{stock_code} 시가대비 조회 성공: 현재가={current_price_str}, 시가={open_price_str}, "
                f"시가대비={display_vs_open_price_formatted}원 ({percentage_change_vs_open_formatted})"
            )
        else:
            # 조회 실패 시 콘솔 및 로그에 오류 기록
            print(f"\n실패: {stock_code} 시가대비 조회.")
            self.logger.error(f"{stock_code} 시가대비 조회 실패: {current_price_result}")

    # run 메서드를 async로 변경
    async def run_async(self):
        """애플리케이션의 메인 비동기 루프를 실행합니다."""
        running = True
        while running:
            self._display_menu()
            choice = await asyncio.to_thread(input, "원하는 작업을 선택하세요 (숫자 입력): ")
            choice = choice.strip()
            running = await self._execute_action(choice)
            if running:
                await asyncio.to_thread(input, "계속하려면 Enter를 누르세요...")
