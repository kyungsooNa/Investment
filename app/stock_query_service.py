# app/stock_query_service.py
import asyncio
from common.types import ErrorCode, ResCommonResponse
from typing import List, Dict

class StockQueryService:
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
        current_price_result : ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)
        if current_price_result and current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            print(f"\n{stock_code} 현재가: {current_price_result.data}")
            self.logger.info(f"{stock_code} 현재가 조회 성공: {current_price_result.data}")
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result.data}")

    async def handle_get_account_balance(self):
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")
        account_balance : ResCommonResponse = await self.trading_service.get_account_balance()
        if account_balance and account_balance.rt_cd == ErrorCode.SUCCESS.value:
            print(f"\n계좌 잔고: {account_balance}")
            self.logger.info(f"계좌 잔고 조회 성공: {account_balance.data}")
        else:
            print(f"\n계좌 잔고 조회 실패.")
            self.logger.error(f"계좌 잔고 조회 실패: {account_balance.data}")

    async def handle_get_top_market_cap_stocks(self, market_code, count: int = None):
        """시가총액 상위 종목 조회 요청 및 결과 출력 (전체 목록)."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")
        top_market_cap_stocks : ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code, count)

        if top_market_cap_stocks and top_market_cap_stocks.rt_cd == ErrorCode.SUCCESS.value:
            print(f"성공: 시가총액 상위 종목 목록:")
            for stock_info in top_market_cap_stocks.data:
                print(f"  순위: {getattr(stock_info, 'data_rank', '')}, "
                      f"종목명: {getattr(stock_info, 'hts_kor_isnm', '')}, "
                      f"시가총액: {getattr(stock_info, 'stck_avls', '')}, "
                      f"현재가: {getattr(stock_info, 'stck_prpr', '')}")
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}), 결과: {top_market_cap_stocks}")
        else:
            print(f"실패: 시가총액 상위 종목 조회.")
            self.logger.error(f"실패: 시가총액 상위 종목 조회: {top_market_cap_stocks}")


    async def handle_get_top_10_market_cap_stocks_with_prices(self):
        print("\n--- 시가총액 1~10위 종목 현재가 조회 시도 ---")
        self.logger.info("시가총액 1~10위 종목 현재가 조회 시도")
        try:
            response : ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
            stocks_data : List = response.data

            if response.rt_cd != ErrorCode.SUCCESS.value: # None은 조회 자체의 실패로 간주
                print("\n실패: 시가총액 1~10위 종목 현재가 조회.")
                self.logger.error("시가총액 1~10위 종목 현재가 조회 실패: None 반환됨")
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="조회 실패: 데이터 None",
                    data=None
                )
            elif not stocks_data: # 빈 리스트는 조회 성공, 결과 없음으로 간주
                # 성공했으나 데이터 없음
                print("\n성공: 시가총액 1~10위 종목 현재가:")
                print("  조회된 종목이 없습니다.")
                self.logger.info("조회된 종목이 없습니다.")
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="조회 성공 (종목 없음)",
                    data=[]
                )
            else: # 종목이 있는 경우
                # 정상 조회
                print("\n성공: 시가총액 1~10위 종목 현재가:")
                for stock in stocks_data:
                    rank = stock.get('rank', 'N/A')
                    name = stock.get('name', 'N/A')
                    code = stock.get('code', 'N/A')
                    price = stock.get('current_price', 'N/A')
                    print(f"  순위: {rank}, 종목명: {name}, 종목코드: {code}, 현재가: {price}원")

                self.logger.info(f"시가총액 1~10위 종목 현재가 조회 성공: {len(stocks_data)}개 종목")
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="조회 성공",
                    data=stocks_data
                )

        except Exception as e:
            print(f"\n실패: 시가총액 1~10위 종목 현재가 조회.")
            self.logger.error(f"시가총액 1~10위 종목 현재가 조회 중 오류 발생: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"예외 발생: {e}",
                data=None
            )

    async def handle_display_stock_change_rate(self, stock_code):
        """
        주식 전일대비 등락률을 조회하고 콘솔에 출력합니다.
        TradingService를 통해 현재 주가 정보를 가져와 등락률을 표시합니다.
        :param stock_code: 조회할 주식 종목코드 (예: "005930")
        """
        print(f"\n--- {stock_code} 전일대비 등락률 조회 ---")

        current_price_result : ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result and current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            output_data = current_price_result.data
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
                    else:
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

        current_price_result : ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result and current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            output_data : Dict = current_price_result.data
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
            top_stocks_response : ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code)

            if not top_stocks_response or top_stocks_response.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"시가총액 상위 종목 목록 조회 실패: {top_stocks_response}")
                return None

            top_stocks_list : List = top_stocks_response.data
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
                stock_code = stock_info.mksc_shrn_iscd
                stock_name = stock_info.hts_kor_isnm

                if stock_code:
                    current_price_response : ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)
                    current_checked_count += 1
                    print(f"\r조회 중... {current_checked_count}/{len(top_stocks_to_check)}", end="")

                    if current_price_response and current_price_response.rt_cd == ErrorCode.SUCCESS.value:
                        output_data = current_price_response.data
                        prdy_vrss_sign = output_data.prdy_vrss_sign  # 전일대비 부호
                        stck_prpr = output_data.stck_prpr  # 현재가
                        prdy_ctrt = output_data.prdy_ctrt  # 전일대비율

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

    async def handle_yesterday_upper_limit_stocks(self, market_code="0000", limit: int = 300):
        """
        시가총액 상위 종목 중 전일 상한가에 도달했던 종목을 조회하여 출력합니다.
        trading_service 내부의 get_top_market_cap_stocks_code 및 get_yesterday_upper_limit_stocks 사용.
        """
        print(f"\n--- 전일 상한가 종목 조회 (상위 {limit}개) ---")
        self.logger.info(f"Service - 전일 상한가 종목 조회 요청 (시장 코드: {market_code}, 수량: {limit})")

        try:
            top_codes_response : ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code)

            if not isinstance(top_codes_response, dict) or top_codes_response.get('rt_cd') != '0':
                msg = top_codes_response.get("msg1", "조회 실패") if isinstance(top_codes_response, dict) else "응답 오류"
                print(f"전일 상한가 종목 조회 실패: {msg}")
                self.logger.warning(f"상위 종목 조회 실패: {top_codes_response}")
                return

            top_stock_codes = [
                item.get("mksc_shrn_iscd")
                for item in top_codes_response.get("output", [])[:limit]
                if "mksc_shrn_iscd" in item
            ]

            if not top_stock_codes:
                print("전일 상한가 종목 조회 대상이 없습니다.")
                self.logger.info("조회된 시가총액 종목 코드 없음.")
                return

            upper_limit_stocks = await self.trading_service.get_yesterday_upper_limit_stocks(top_stock_codes)

            if not upper_limit_stocks:
                print("현재 전일 상한가에 해당하는 종목이 없습니다.")
                self.logger.info("전일 상한가 종목 없음.")
            else:
                print("\n--- 전일 상한가 종목 ---")
                for stock in upper_limit_stocks:
                    print(f"  {stock['name']} ({stock['code']}): {stock['price']}원 (등락률: +{stock['change_rate']}%)")
                self.logger.info(f"전일 상한가 종목 조회 성공. 총 {len(upper_limit_stocks)}개")
        except Exception as e:
            print(f"전일 상한가 종목 조회 중 오류 발생: {e}")
            self.logger.error(f"전일 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)

    async def handle_current_upper_limit_stocks(self, market_code="0000"):
        """
        전체 종목 중 현재 상한가에 도달한 종목을 조회하여 출력합니다.
        trading_service 내부의 get_all_stocks_code 및 get_current_upper_limit_stocks 사용.
        """
        print("\n--- 현재 상한가 종목 조회 ---")
        self.logger.info(f"Service - 현재 상한가 종목 조회 요청 (시장 코드: {market_code})")

        try:
            # 전체 종목 코드 조회
            all_stock_codes : ResCommonResponse = await self.trading_service.get_all_stocks_code(market_code)

            if not all_stock_codes:
                print("전체 종목 코드 조회 실패 또는 결과 없음.")
                self.logger.warning("전체 종목 코드 없음.")
                return

            # 현재 상한가 종목 필터링
            upper_limit_stocks = await self.trading_service.get_current_upper_limit_stocks(all_stock_codes)

            if not upper_limit_stocks:
                print("현재 상한가에 해당하는 종목이 없습니다.")
                self.logger.info("현재 상한가 종목 없음.")
            else:
                print("\n--- 현재 상한가 종목 ---")
                for stock in upper_limit_stocks:
                    print(f"  {stock['name']} ({stock['code']}): {stock['price']}원 (등락률: +{stock['change_rate']}%)")
                self.logger.info(f"현재 상한가 종목 조회 성공. 총 {len(upper_limit_stocks)}개")

        except Exception as e:
            print(f"현재 상한가 종목 조회 중 오류 발생: {e}")
            self.logger.error(f"현재 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)
