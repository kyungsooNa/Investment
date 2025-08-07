# app/stock_query_service.py
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResBasicStockInfo, ResMarketCapStockItem, \
    ResStockFullInfoApiOutput
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
        current_price_result: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)
        if current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            print(f"\n{stock_code} 현재가: {current_price_result.data}")
            self.logger.info(f"{stock_code} 현재가 조회 성공: {current_price_result.data}")
        else:
            print(f"\n{stock_code} 현재가 조회 실패.")
            self.logger.error(f"{stock_code} 현재가 조회 실패: {current_price_result.data}")

    async def handle_get_account_balance(self) -> ResCommonResponse:
        """계좌 잔고 조회 요청 및 결과 출력."""
        print("\n--- 계좌 잔고 조회 ---")
        return await self.trading_service.get_account_balance()

    async def handle_get_top_market_cap_stocks_code(self, market_code, count: int = None) -> ResCommonResponse:
        """시가총액 상위 종목 조회 요청 및 결과 출력 (전체 목록)."""
        print("\n--- 시가총액 상위 종목 조회 시도 ---")
        top_market_cap_stocks: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
            market_code, count)

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

        return top_market_cap_stocks

    async def handle_get_top_10_market_cap_stocks_with_prices(self) -> ResCommonResponse:
        print("\n--- 시가총액 1~10위 종목 현재가 조회 시도 ---")
        self.logger.info("시가총액 1~10위 종목 현재가 조회 시도")
        try:
            response: ResCommonResponse = await self.trading_service.get_top_10_market_cap_stocks_with_prices()
            stocks_data: List = response.data

            if response.rt_cd != ErrorCode.SUCCESS.value:  # None은 조회 자체의 실패로 간주
                print("\n실패: 시가총액 1~10위 종목 현재가 조회.")
                self.logger.error(response.msg1)
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="조회 실패: 데이터 None",
                    data=None
                )
            elif not stocks_data:  # 빈 리스트는 조회 성공, 결과 없음으로 간주
                # 성공했으나 데이터 없음
                print("\n성공: 시가총액 1~10위 종목 현재가:")
                print("  조회된 종목이 없습니다.")
                self.logger.info("조회된 종목이 없습니다.")
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="조회 성공 (종목 없음)",
                    data=[]
                )
            else:  # 종목이 있는 경우
                # 정상 조회
                print("\n성공: 시가총액 1~10위 종목 현재가:")
                for stock in stocks_data:
                    if not isinstance(stock, ResMarketCapStockItem):
                        raise TypeError(f"Stock가 ResMarketCapStockItem 타입이 아님: {type(stock)}")

                    rank = stock.rank
                    name = stock.name
                    code = stock.code
                    price = stock.current_price
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

        current_price_result: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            output_data = current_price_result.data.get("output") or {}
            current_price = output_data.stck_prpr
            change_val_str = output_data.prdy_vrss
            change_sign_code = output_data.prdy_vrss_sign
            change_rate_str = output_data.prdy_ctrt

            # 부호 코드에 따른 실제 부호 문자열 결정
            actual_change_sign = self._get_sign_from_code(change_sign_code)

            try:
                float(change_val_str)
                display_change_val = f"{actual_change_sign}{change_val_str}"
            except (ValueError, TypeError):
                display_change_val = change_val_str  # 그냥 "ABC"

            # 0원일 경우 부호 없이 0으로 표시
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

        current_price_result: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)

        if current_price_result and current_price_result.rt_cd == ErrorCode.SUCCESS.value:
            output_data: ResStockFullInfoApiOutput = current_price_result.data.get("output") or {}
            current_price_str = output_data.stck_prpr
            open_price_str = output_data.stck_oprc

            try:
                try:
                    current_price_float = float(current_price_str) if current_price_str not in (None, 'N/A') else None
                    open_price_float = float(open_price_str) if open_price_str not in (None, 'N/A') else None
                except (ValueError, TypeError):
                    self.logger.warning(
                        f"{stock_code} 시가대비 조회 실패: 가격 파싱 오류 (현재가={current_price_str}, 시가={open_price_str})")
                    return

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
            except (TypeError, ValueError):
                print("⚠️ 시가 또는 현재가 정보가 유효하지 않아 계산할 수 없습니다.")
                return
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
        if self.trading_service._env.is_paper_trading:  # trading_service 내부의 env 확인
            self.logger.warning("Service - 상한가 종목 조회는 모의투자를 지원하지 않습니다.")
            print("WARNING: 모의투자 환경에서는 상한가 종목 조회를 지원하지 않습니다.\n")
            return {"rt_cd": "1", "msg1": "모의투자 미지원 API입니다."}  # 오류 딕셔너리 반환

        if not self.time_manager.is_market_open():
            self.logger.warning("시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.")
            print("WARNING: 시장이 닫혀 있어 상한가 종목 조회를 수행할 수 없습니다.\n")
            return None  # None 반환하여 상위 호출자에게 실패 알림

        upper_limit_stocks_found = []
        try:
            # 1. 시가총액 상위 종목 목록 조회 (TradingService 위임)
            top_stocks_response: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
                market_code)

            if not top_stocks_response or top_stocks_response.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"시가총액 상위 종목 목록 조회 실패: {top_stocks_response}")
                return None

            top_stocks_list: List = top_stocks_response.data
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
                    current_price_response: ResCommonResponse = await self.trading_service.get_current_stock_price(
                        stock_code)
                    current_checked_count += 1
                    print(f"\r조회 중... {current_checked_count}/{len(top_stocks_to_check)}", end="")

                    if current_price_response.rt_cd == ErrorCode.SUCCESS.value:
                        output_data = current_price_response.data.get("output", {}) if isinstance(
                            current_price_response.data, dict) else {}
                        prdy_vrss_sign = output_data['prdy_vrss_sign']  # 전일대비 부호
                        stck_prpr = output_data['stck_prpr']  # 현재가
                        prdy_ctrt = output_data['prdy_ctrt']  # 전일대비율

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
            top_codes_response: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(
                market_code)

            if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:
                msg = top_codes_response.get("msg1", "조회 실패") if isinstance(top_codes_response, dict) else "응답 오류"
                print(f"전일 상한가 종목 조회 실패: {msg}")
                self.logger.warning(f"상위 종목 조회 실패: {top_codes_response}")
                return

            top_stock_codes = [
                item.mksc_shrn_iscd
                for item in top_codes_response.data
                if isinstance(item, ResTopMarketCapApiItem) and item.mksc_shrn_iscd
            ]

            if not top_stock_codes:
                print("전일 상한가 종목 조회 대상이 없습니다.")
                self.logger.warning("조회된 시가총액 종목 코드 없음.")
                return

            upper_limit_stocks: ResCommonResponse = await self.trading_service.get_yesterday_upper_limit_stocks(
                top_stock_codes)

            if upper_limit_stocks.rt_cd != ErrorCode.SUCCESS.value:
                print("현재 전일 상한가에 해당하는 종목이 없습니다.")
                self.logger.info("전일 상한가 종목 없음.")
            else:
                print("\n--- 전일 상한가 종목 ---")
                for stock in upper_limit_stocks.data:
                    print(f"  {stock['name']} ({stock['code']}): {stock['price']}원 (등락률: +{stock['change_rate']}%)")
                self.logger.info(f"전일 상한가 종목 조회 성공. 총 {len(upper_limit_stocks.data)}개")
        except Exception as e:
            print(f"전일 상한가 종목 조회 중 오류 발생: {e}")
            self.logger.error(f"전일 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)

    async def handle_current_upper_limit_stocks(self):
        """
        전체 종목 중 현재 상한가에 도달한 종목을 조회하여 출력합니다.
        trading_service 내부의 get_all_stocks_code 및 get_current_upper_limit_stocks 사용.
        """
        print("\n--- 현재 상한가 종목 조회 ---")
        self.logger.info("Service - 현재 상한가 종목 조회 요청 ")

        try:
            # 전체 종목 코드 조회
            all_stock_codes: ResCommonResponse = await self.trading_service.get_all_stocks_code()

            if all_stock_codes.rt_cd != ErrorCode.SUCCESS.value:
                print("전체 종목 코드 조회 실패 또는 결과 없음.")
                self.logger.warning("전체 종목 코드 없음.")
                return

            # 현재 상한가 종목 필터링
            if not isinstance(all_stock_codes.data, list):
                self.logger.error("get_all_stock_codes.data 리스트가 아님.")
                return

            upper_limit_stocks: ResCommonResponse = await self.trading_service.get_current_upper_limit_stocks(
                all_stock_codes.data)

            if upper_limit_stocks.rt_cd != ErrorCode.SUCCESS.value:
                print("현재 상한가에 해당하는 종목이 없습니다.")
                self.logger.info("현재 상한가 종목 없음.")
            else:
                print("\n--- 현재 상한가 종목 ---")
                for stock in upper_limit_stocks.data:
                    if not isinstance(stock, ResBasicStockInfo):
                        raise TypeError(f"ResBasicStockInfo 타입이 아님: {type(stock)}")

                    print(f"  {stock.name} ({stock.code}): {stock.current_price}원 (등락률: +{stock.prdy_ctrt}%)")
                self.logger.info(f"현재 상한가 종목 조회 성공. 총 {len(upper_limit_stocks.data)}개")

        except Exception as e:
            print(f"현재 상한가 종목 조회 중 오류 발생: {e}")
            self.logger.error(f"현재 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)

    async def handle_get_asking_price(self, stock_code: str):
        """종목의 실시간 호가 정보 조회 및 출력."""
        print(f"\n--- {stock_code} 실시간 호가 조회 ---")
        self.logger.info(f"Handler - {stock_code} 호가 정보 조회 요청")
        response = await self.trading_service.get_asking_price(stock_code)

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            # 한국투자증권 API는 output1에 호가, output2에 시간외 단일가 정보를 담아 반환합니다.
            quote_data = response.data.get('output1', [])
            print(f"\n성공: {stock_code} 호가 정보")
            print("-" * 40)
            print(f"{'매도잔량':>10s} | {'호가':>10s} | {'매수잔량':>10s}")
            print("-" * 40)
            for i in range(min(len(quote_data), 5)):  # 상위 5개 호가만 출력
                ask_price = quote_data.get(f'askp{i + 1}', 'N/A')
                ask_rem = quote_data.get(f'askp_rsqn{i + 1}', 'N/A')
                bid_price = quote_data.get(f'bidp{i + 1}', 'N/A')
                bid_rem = quote_data.get(f'bidp_rsqn{i + 1}', 'N/A')
                print(f"{ask_rem:>10s} | {ask_price:>10s} |")
                print(f"{'':>23s} | {bid_price:>10s} | {bid_rem:>10s}")
            print("-" * 40)
            self.logger.info(f"{stock_code} 호가 정보 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: {stock_code} 호가 정보 조회. ({msg})")
            self.logger.error(f"{stock_code} 호가 정보 조회 실패: {msg}")

    async def handle_get_time_concluded_prices(self, stock_code: str):
        """종목의 시간대별 체결가 정보 조회 및 출력."""
        print(f"\n--- {stock_code} 시간대별 체결가 조회 ---")
        self.logger.info(f"Handler - {stock_code} 시간대별 체결가 조회 요청")
        response = await self.trading_service.get_time_concluded_prices(stock_code)

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            concluded_data = response.data.get('output', [])
            print(f"\n성공: {stock_code} 시간대별 체결 정보 (최근 10건)")
            print("-" * 50)
            print(f"{'체결시각':>10s} | {'체결가':>12s} | {'전일대비':>10s} | {'체결량':>10s}")
            print("-" * 50)
            for item in concluded_data[:10]:
                trade_time = item.get('stck_cntg_hour', 'N/A')
                price = item.get('stck_prpr', 'N/A')
                change = item.get('prdy_vrss', 'N/A')
                volume = item.get('cntg_vol', 'N/A')
                print(f"{trade_time:>10s} | {price:>12s} | {change:>10s} | {volume:>10s}")
            print("-" * 50)
            self.logger.info(f"{stock_code} 시간대별 체결가 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: {stock_code} 시간대별 체결가 조회. ({msg})")
            self.logger.error(f"{stock_code} 시간대별 체결가 조회 실패: {msg}")

    async def handle_search_stocks_by_keyword(self, keyword: str):
        """키워드로 종목 검색 및 결과 출력."""
        print(f"\n--- '{keyword}' 키워드 종목 검색 ---")
        self.logger.info(f"Handler - '{keyword}' 키워드 종목 검색 요청")
        response = await self.trading_service.search_stocks_by_keyword(keyword)

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            search_results = response.data.get('output', [])
            if not search_results:
                print(f"\n'{keyword}'에 대한 검색 결과가 없습니다.")
                self.logger.info(f"'{keyword}' 키워드 검색 결과 없음")
                return

            print(f"\n성공: '{keyword}' 검색 결과")
            print("-" * 40)
            print(f"{'종목코드':<15} | {'종목명'}")
            print("-" * 40)
            for item in search_results:
                code = item.get('iscd', 'N/A')
                name = item.get('iscd_nm', 'N/A')
                print(f"{code:<15} | {name}")
            print("-" * 40)
            self.logger.info(f"'{keyword}' 키워드 종목 검색 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: 종목 검색. ({msg})")
            self.logger.error(f"종목 검색 실패: {msg}")

    async def handle_get_top_stocks(self, category: str):
        """상위 종목 조회 및 출력 (상승률, 하락률, 거래량, 외국인순매수)."""
        category_map = {
            "rise": ("상승률", self.trading_service.get_top_rise_fall_stocks, True),
            "fall": ("하락률", self.trading_service.get_top_rise_fall_stocks, False),
            "volume": ("거래량", self.trading_service.get_top_volume_stocks, None),
            "foreign": ("외국인 순매수", self.trading_service.get_top_foreign_buying_stocks, None),
        }

        if category not in category_map:
            print(f"\n오류: 지원하지 않는 카테고리입니다. (사용 가능: rise, fall, volume, foreign)")
            return

        title, service_func, param = category_map[category]
        print(f"\n--- {title} 상위 종목 조회 ---")
        self.logger.info(f"Handler - {title} 상위 종목 조회 요청")

        if param is not None:
            response = await service_func(param)
        else:
            response = await service_func()

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            stock_list = response.data.get('output', [])
            print(f"\n성공: {title} 상위 10개 종목")
            print("-" * 60)
            print(f"{'순위':>4s} | {'종목명':<20s} | {'현재가':>10s} | {'등락률(%)':>10s}")
            print("-" * 60)
            for item in stock_list[:10]:
                rank = item.get('data_rank', 'N/A')
                name = item.get('hts_kor_isnm', 'N/A')
                price = item.get('stck_prpr', 'N/A')
                rate = item.get('prdy_ctrt', 'N/A')
                print(f"{rank:>4s} | {name:<20s} | {price:>10s} | {rate:>10s}")
            print("-" * 60)
            self.logger.info(f"{title} 상위 종목 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: {title} 상위 종목 조회. ({msg})")
            self.logger.error(f"{title} 상위 종목 조회 실패: {msg}")

    async def handle_get_stock_news(self, stock_code: str):
        """종목 뉴스 조회 및 출력."""
        print(f"\n--- {stock_code} 종목 뉴스 조회 ---")
        self.logger.info(f"Handler - {stock_code} 종목 뉴스 조회 요청")
        response = await self.trading_service.get_stock_news(stock_code)

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            news_list = response.data.get('output', [])
            if not news_list:
                print(f"\n{stock_code}에 대한 뉴스가 없습니다.")
                self.logger.info(f"{stock_code} 뉴스 없음")
                return

            print(f"\n성공: {stock_code} 최신 뉴스 (최대 5건)")
            print("-" * 70)
            for item in news_list[:5]:
                news_date = item.get('news_dt', '')
                news_time = item.get('news_tm', '')
                title = item.get('news_tl', 'N/A')
                print(f"[{news_date} {news_time}] {title}")
            print("-" * 70)
            self.logger.info(f"{stock_code} 종목 뉴스 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: {stock_code} 종목 뉴스 조회. ({msg})")
            self.logger.error(f"{stock_code} 종목 뉴스 조회 실패: {msg}")

    async def handle_get_etf_info(self, etf_code: str):
        """ETF 정보 조회 및 출력."""
        print(f"\n--- {etf_code} ETF 정보 조회 ---")
        self.logger.info(f"Handler - {etf_code} ETF 정보 조회 요청")
        response = await self.trading_service.get_etf_info(etf_code)

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            etf_info = response.data.get('output', {})
            name = etf_info.get('etf_rprs_bstp_kor_isnm', 'N/A')
            price = etf_info.get('stck_prpr', 'N/A')
            nav = etf_info.get('nav', 'N/A')
            market_cap = etf_info.get('stck_llam', 'N/A')

            print(f"\n성공: {name} ({etf_code})")
            print("-" * 40)
            print(f"  현재가: {price} 원")
            print(f"  NAV: {nav}")
            print(f"  시가총액: {market_cap} 원")
            print("-" * 40)
            self.logger.info(f"{etf_code} ETF 정보 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            print(f"\n실패: {etf_code} ETF 정보 조회. ({msg})")
            self.logger.error(f"{etf_code} ETF 정보 조회 실패: {msg}")

    async def handle_realtime_stream(self, stock_codes: list[str], fields: list[str], duration: int = 30):
        """
        TradingService를 통해 실시간 스트림을 구독 및 처리합니다.

        :param stock_codes: 실시간 데이터를 구독할 종목 코드 리스트
        :param fields: "price", "quote" 중 원하는 실시간 데이터 타입 리스트
        :param duration: 실시간 스트리밍을 유지할 시간 (초)
        """
        self.logger.info(f"StockQueryService - 실시간 스트림 요청: 종목={stock_codes}, 필드={fields}, 시간={duration}s")
        await self.trading_service.handle_realtime_stream(stock_codes, fields, duration)
