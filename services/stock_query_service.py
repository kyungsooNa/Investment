# app/stock_query_service.py
from __future__ import annotations
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResBasicStockInfo, ResMarketCapStockItem, \
    ResStockFullInfoApiOutput
from config.DynamicConfig import DynamicConfig
from typing import List, Dict, Optional, Literal


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
        self.logger.info(f"Service - {stock_code} 현재가 조회 요청")
        resp: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)

        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            msg = resp.msg1 if resp else "응답 없음"
            self.logger.error(f"{stock_code} 현재가 조회 실패: {msg}")
            return ResCommonResponse(
                rt_cd=(resp.rt_cd if resp else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        # --- output 추출 및 통일화(dict) ---
        output = (resp.data or {}).get("output") if isinstance(resp.data, dict) else resp.data

        if isinstance(output, ResStockFullInfoApiOutput):
            raw = output.to_dict()          # 데이터클래스 → dict
        elif isinstance(output, dict):
            raw = output                    # 이미 dict
        elif isinstance(output, list) and output and isinstance(output[0], dict):
            raw = output[0]                 # 리스트면 0번만 사용(일반적으로 단일 레코드)
        else:
            raw = {}                        # 방어

        price = raw.get("stck_prpr") or raw.get("prpr") or raw.get("current") or "N/A"
        change = raw.get("prdy_vrss") or raw.get("change") or "N/A"
        rate   = raw.get("prdy_ctrt") or raw.get("rate")   or "N/A"
        time_  = raw.get("stck_cntg_hour") or raw.get("time") or "N/A"
        open_  = raw.get("stck_oprc") or raw.get("open")
        high   = raw.get("stck_hgpr") or raw.get("high")
        low    = raw.get("stck_lwpr") or raw.get("low")
        prev   = raw.get("stck_prdy_clpr") or raw.get("prev_close")
        vol    = raw.get("cntg_vol") or raw.get("volume")

        view = {
            "code": stock_code,
            "price": price,
            "change": change,
            "rate": rate,
            "time": time_,
            "open": open_ or "N/A",
            "high": high or "N/A",
            "low": low or "N/A",
            "prev_close": prev or "N/A",
            "volume": vol or "N/A",
        }
        self.logger.info(f"{stock_code} 현재가 조회 성공")
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view)

    async def handle_get_account_balance(self) -> ResCommonResponse:
        """계좌 잔고 조회 요청 및 결과 출력."""
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
        self.logger.info("Service - 현재 상한가 종목 조회 요청 ")

        try:
            rise_res: ResCommonResponse = await self.trading_service.get_top_rise_fall_stocks(rise=True)
            if rise_res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.warning("상승률 조회 실패.")
                return rise_res

            upper_limit_stocks: ResCommonResponse = await self.trading_service.get_current_upper_limit_stocks(
                rise_res.data)

            if upper_limit_stocks.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.info("현재 상한가 종목 없음.")

            return upper_limit_stocks

        except Exception as e:
            self.logger.error(f"현재 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)
            raise

    async def handle_get_asking_price(self, stock_code: str, depth: int = 10):
        """종목의 실시간 호가 정보 조회 및 출력."""
        self.logger.info(f"Service - {stock_code} 호가 정보 조회 요청")
        response = await self.trading_service.get_asking_price(stock_code)

        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{stock_code} 호가 정보 조회 실패: {msg}")
            return ResCommonResponse(
                rt_cd=(response.rt_cd if response else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        raw1 = (response.data or {}).get("output1") or {}
        # 일부 구현에서 list로 줄 수도 있으니 방어
        if isinstance(raw1, list):
            raw1 = raw1[0] if raw1 else {}

        rows = []
        for i in range(1, depth + 1):
            rows.append({
                "level": i,
                "ask_price": raw1.get(f"askp{i}", "N/A"),
                "ask_rem":   raw1.get(f"askp_rsqn{i}", "N/A"),
                "bid_price": raw1.get(f"bidp{i}", "N/A"),
                "bid_rem":   raw1.get(f"bidp_rsqn{i}", "N/A"),
            })

        view_model = {
            "code": stock_code,
            "rows": rows,
            # 필요시 추가 필드들(예: 현재가/참고값 등)
            "meta": {
                "prpr": raw1.get("stck_prpr"),
                "time": raw1.get("aplm_hour") or raw1.get("stck_cntg_hour"),
            }
        }

        self.logger.info(f"{stock_code} 호가 정보 조회 성공")
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view_model)

    async def handle_get_time_concluded_prices(self, stock_code: str):
        """종목의 시간대별 체결가 정보 조회 및 출력."""
        self.logger.info(f"Service - {stock_code} 시간대별 체결가 조회 요청")
        response = await self.trading_service.get_time_concluded_prices(stock_code)

        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{stock_code} 시간대별 체결가 조회 실패: {msg}")
            return ResCommonResponse(
                rt_cd=(response.rt_cd if response else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        raw = (response.data or {}).get("output") or []
        if isinstance(raw, dict):
            raw = [raw]

        rows = []
        for item in raw:
            rows.append({
                "time":   item.get("stck_cntg_hour", "N/A"),
                "price":  item.get("stck_prpr", "N/A"),
                "change": item.get("prdy_vrss", "N/A"),
                "volume": item.get("cntg_vol", "N/A"),
            })

        view_model = {"code": stock_code, "rows": rows}
        self.logger.info(f"{stock_code} 시간대별 체결가 조회 성공")
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view_model)

    async def handle_get_top_stocks(self, category: str) -> ResCommonResponse:
        """상위 종목 조회 및 출력 (상승률, 하락률, 거래량, 외국인순매수)."""
        category_map = {
            "rise": ("상승률", self.trading_service.get_top_rise_fall_stocks, True),
            "fall": ("하락률", self.trading_service.get_top_rise_fall_stocks, False),
            "volume": ("거래량", self.trading_service.get_top_volume_stocks, None),
            # "foreign": ("외국인 순매수", self.trading_service.get_top_foreign_buying_stocks, None),
        }

        if category not in category_map:
            self.logger.error(f"지원하지 않는 카테고리: {category}")
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=f"지원하지 않는 카테고리: {category}",
                data=None,
            )

        title, service_func, param = category_map[category]
        self.logger.info(f"Handler - {title} 상위 종목 조회 요청")

        response = await (service_func(param) if param is not None else service_func())

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(f"{title} 상위 종목 조회 성공")
        else:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{title} 상위 종목 조회 실패: {msg}")

        return response

    async def handle_get_etf_info(self, etf_code: str):
        """
        ETF 정보를 TradingService에서 받아와 출력용 뷰모델로 가공하여 반환만 한다.
        출력은 cli_view에 위임한다.
        """
        self.logger.info(f"Service - {etf_code} ETF 정보 조회 요청")

        response = await self.trading_service.get_etf_info(etf_code)

        # 실패면 그대로 전달 (cli_view에서 실패 출력)
        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{etf_code} ETF 정보 조회 실패: {msg}")
            # data에는 최소한 식별 정보만 넣어두면 뷰에서 에러 메시지에 활용 가능
            return ResCommonResponse(
                rt_cd=response.rt_cd if response else ErrorCode.FAIL.value,
                msg1=msg,
                data={"code": etf_code}
            )

        # 성공: 출력용 뷰모델로 가공
        raw = response.data.get("output", {}) if response.data else {}
        view_model = {
            "code": etf_code,
            "name": raw.get("etf_rprs_bstp_kor_isnm", "N/A"),
            "price": raw.get("stck_prpr", "N/A"),
            "nav": raw.get("nav", "N/A"),
            "market_cap": raw.get("stck_llam", "N/A"),
        }

        self.logger.info(f"{etf_code} ETF 정보 조회 성공")
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data=view_model
        )


    async def handle_realtime_stream(self, stock_codes: list[str], fields: list[str], duration: int = 30):
        """
        TradingService를 통해 실시간 스트림을 구독 및 처리합니다.

        :param stock_codes: 실시간 데이터를 구독할 종목 코드 리스트
        :param fields: "price", "quote" 중 원하는 실시간 데이터 타입 리스트
        :param duration: 실시간 스트리밍을 유지할 시간 (초)
        """
        self.logger.info(f"StockQueryService - 실시간 스트림 요청: 종목={stock_codes}, 필드={fields}, 시간={duration}s")
        await self.trading_service.handle_realtime_stream(stock_codes, fields, duration)

    async def get_ohlcv(self, stock_code: str, period: str = "D") -> ResCommonResponse:
        """
        OHLCV 데이터를 TradingService에서 받아 그대로 반환.
        (출력은 하지 않음: viewer로 위임)
        """
        self.logger.info(f"ServiceHandler - {stock_code} OHLCV 데이터 요청 period={period}")
        try:
            resp: ResCommonResponse = await self.trading_service.get_ohlcv(
                stock_code, period=period
            )
            return resp
        except Exception as e:
            self.logger.error(f"{stock_code} OHLCV 데이터 처리 중 오류: {e}", exc_info=True)
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=[])

    async def get_recent_daily_ohlcv(self, stock_code: str, limit: int = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE) -> ResCommonResponse:
        """
        타겟 종목의 최근 일봉을 limit개 반환.
        TradingService.get_recent_daily_ohlcv를 래핑하여 ResCommonResponse 형태로 통일.
        """
        try:
            rows = await self.trading_service.get_recent_daily_ohlcv(stock_code, limit=limit)
            if not rows:
                return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="데이터 없음", data=[])
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=rows)
        except Exception as e:
            self.logger.error(f"[OHLCV] {stock_code} 조회 실패: {e}", exc_info=True)
            return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1=str(e), data=[])

    async def get_intraday_minutes_today(self, stock_code: str, *, input_hour_1: str) -> ResCommonResponse:
        """
        당일 분봉 조회. TradingService 위임.
        """
        return await self.trading_service.get_intraday_minutes_today(
            stock_code=stock_code, input_hour_1=input_hour_1
        )

    async def get_intraday_minutes_by_date(
        self, stock_code: str, *, input_date_1: str, input_hour_1: str = ""
    ) -> ResCommonResponse:
        """
        일별(특정 일자) 분봉 조회. TradingService 위임.
        """
        return await self.trading_service.get_intraday_minutes_by_date(
            stock_code=stock_code, input_date_1=input_date_1, input_hour_1=input_hour_1
        )

    async def get_day_intraday_minutes_list(
        self,
        stock_code: str,
        *,
        date_ymd: Optional[str] = None,                                    # None이면 '오늘'(KST) 조회
        session: Literal["REGULAR", "EXTENDED"] = "REGULAR",                # REGULAR=09:00~15:30, EXTENDED=08:00~20:00
        start_hhmmss: Optional[str] = None,
        end_hhmmss: Optional[str] = None,
        max_batches: int = 200
    ) -> List[Dict]:
        """
        하루치 분봉(분봉 행 dict)의 '정규화된 리스트'를 반환한다. (출력은 호출부/cli_view에서)
        - date_ymd=None: 오늘(KST) → get_intraday_minutes_today(배치당 30개; 모의/실전 모두 가능)
        - date_ymd=YYYYMMDD: 지정일 → get_intraday_minutes_by_date(배치당 100개; 실전 전용)
        - 시간 범위: session 프리셋으로 선택하거나 start/end를 직접 지정 가능
        - 반환: 시간 오름차순(HHMMSS) 정렬된 리스트. 각 행은 최소 다음 키를 포함:
          'stck_bsop_date'(YYYYMMDD), 'stck_cntg_hour'(HHMMSS), 나머지는 원본 필드 유지
        """
        # 세션 범위 결정
        if not start_hhmmss or not end_hhmmss:
            if session.upper() == "EXTENDED":
                start_hhmmss = start_hhmmss or "080000"
                end_hhmmss   = end_hhmmss   or "200000"
            else:
                start_hhmmss = start_hhmmss or "090000"
                end_hhmmss   = end_hhmmss   or "153000"

        start_hhmmss = self.time_manager.to_hhmmss(start_hhmmss)
        end_hhmmss   = self.time_manager.to_hhmmss(end_hhmmss)

        # 조회 날짜
        if date_ymd:
            ymd = date_ymd
        else:
            now_kst = self.time_manager.get_current_kst_time()
            ymd = now_kst.strftime("%Y%m%d")

        # 배치 호출 함수 선택
        async def _fetch_batch(cursor_hhmmss: str):
            cursor_hhmmss = self.time_manager.to_hhmmss(cursor_hhmmss)
            if self.trading_service._env.is_paper_trading:
                # 오늘(모의/실전; 배치당 30개)
                return await self.get_intraday_minutes_today(
                    stock_code, input_hour_1=cursor_hhmmss
                )
            else:
                # 지정일(실전 전용; 배치당 100개)
                return await self.get_intraday_minutes_by_date(
                    stock_code, input_date_1=ymd, input_hour_1=cursor_hhmmss
                )

        def _extract_rows(resp_obj) -> list[dict]:
            """resp.data가 list 또는 dict(output2/rows/data 키)인 모든 경우를 수용."""
            data = getattr(resp_obj, "data", None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                rows = data.get("output2") or data.get("rows") or data.get("data") or []
                return rows if isinstance(rows, list) else []
            return []

        # 커서: end부터 과거로 내려가며 수집
        cursor = end_hhmmss
        seen: set[tuple[str, str]] = set()   # (date, hhmmss)
        collected: List[Dict] = []
        batches = 0

        while batches < max_batches:
            batches += 1
            resp = await _fetch_batch(cursor)
            if not resp or str(getattr(resp, "rt_cd", "1")) != "0":
                break

            rows = _extract_rows(resp)
            if not rows:
                break

            min_time_in_batch = None
            added = 0

            for row in rows:
                d = str(row.get("stck_bsop_date") or ymd)
                t = self.time_manager.to_hhmmss(row.get("stck_cntg_hour") or "")
                # 범위 필터
                if t < start_hhmmss or t > end_hhmmss:
                    continue
                key = (d, t)
                if key in seen:
                    continue
                seen.add(key)

                norm = dict(row)
                norm["stck_bsop_date"] = d
                norm["stck_cntg_hour"] = t
                collected.append(norm)
                added += 1

                if (min_time_in_batch is None) or (t < min_time_in_batch):
                    min_time_in_batch = t

            if added == 0:
                if min_time_in_batch:
                    cursor = self.time_manager.dec_minute(min_time_in_batch, 1)
                    if cursor < start_hhmmss:
                        break
                    continue
                break

            if min_time_in_batch:
                cursor = self.time_manager.dec_minute(min_time_in_batch, 1)
                if cursor < start_hhmmss:
                    break
            else:
                break

        # 최종 정렬(과거→현재)
        collected.sort(key=lambda r: r.get("stck_cntg_hour", ""))

        return collected