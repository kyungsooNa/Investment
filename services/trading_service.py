# services/trading_service.py
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from core.time_manager import TimeManager
import logging
from brokers.broker_api_wrapper import BrokerAPIWrapper
from typing import List
from datetime import datetime, timedelta
import asyncio

# common/types에서 모든 ResTypedDict와 ErrorCode 임포트
from common.types import (
    ResPriceSummary, ResCommonResponse, ErrorCode, ResMarketCapStockItem,
    ResTopMarketCapApiItem,ResBasicStockInfo
)

class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, broker_api_wrapper: BrokerAPIWrapper, env: KoreaInvestApiEnv, logger=None,
                 time_manager: TimeManager = None):
        self._broker_api_wrapper = broker_api_wrapper
        self._env = env  # env는 그대로 유지
        self._logger = logger if logger else logging.getLogger(__name__)
        self._time_manager = time_manager

    def _default_realtime_message_handler(self, data):
        """
        웹소켓으로부터 수신된 실시간 데이터를 처리하는 기본 핸들러.
        여기서 데이터를 파싱하여 콘솔에 보기 좋게 출력합니다.
        """
        self._logger.info(f"실시간 데이터 수신: Type={data.get('type')}, TR_ID={data.get('tr_id')}, Data={data.get('data')}")

        # 주식 체결 데이터 (H0STCNT0) 처리
        if data.get('type') == 'realtime_price':
            realtime_data = data.get('data', {})
            stock_code = realtime_data.get('MKSC_SHRN_ISCD', 'N/A')
            current_price = realtime_data.get('STCK_PRPR', 'N/A')
            change = realtime_data.get('PRDY_VRSS', 'N/A')
            change_sign = realtime_data.get('PRDY_VRSS_SIGN', 'N/A')
            change_rate = realtime_data.get('PRDY_CTRT', 'N/A')
            cumulative_volume = realtime_data.get('ACML_VOL', 'N/A')
            trade_time = realtime_data.get('STCK_CNTG_HOUR', 'N/A')

            display_message = (
                f"\r[실시간 체결 - {trade_time}] 종목: {stock_code}: 현재가 {current_price}원, "
                f"전일대비: {change_sign}{change} ({change_rate}%), 누적량: {cumulative_volume}"
            )
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
            self._logger.info(
                f"실시간 체결 데이터: {stock_code} 현재가={current_price}, 전일대비={change_sign}{change}({change_rate}%), 누적량={cumulative_volume}")

        # 주식 호가 데이터 (H0STASP0) 처리
        elif data.get('type') == 'realtime_quote':
            quote_data = data.get('data', {})
            stock_code = quote_data.get('유가증권단축종목코드', 'N/A')
            askp1 = quote_data.get('매도호가1', 'N/A')
            bidp1 = quote_data.get('매수호가1', 'N/A')
            trade_time = quote_data.get('영업시간', 'N/A')

            display_message = f"[실시간 호가 - {trade_time}] 종목: {stock_code}: 매도1호가: {askp1}, 매수1호가: {bidp1}"
            print(f"\r{display_message}{' ' * (80 - len(display_message))}", end="")
            self._logger.info(f"실시간 호가 데이터: {stock_code} 매도1={askp1}, 매수1={bidp1}")

        elif data.get('type') == 'signing_notice':
            notice_data = data.get('data', {})
            order_num = notice_data.get('주문번호', 'N/A')
            trade_qty = notice_data.get('체결수량', 'N/A')
            trade_price = notice_data.get('체결단가', 'N/A')
            trade_time = notice_data.get('주식체결시간', 'N/A')

            print(f"\n[체결통보] 주문: {order_num}, 수량: {trade_qty}, 단가: {trade_price}, 시간: {trade_time}")
            self._logger.info(f"체결통보: 주문={order_num}, 수량={trade_qty}, 단가={trade_price}")
        else:
            self._logger.debug(f"처리되지 않은 실시간 메시지: {data.get('tr_id')} - {data}")


    async def connect_websocket(self, on_message_callback=None):
        """웹소켓 연결을 비동기로 시작합니다."""
        return await self._broker_api_wrapper.connect_websocket(
            on_message_callback=on_message_callback if on_message_callback else self._default_realtime_message_handler)

    async def disconnect_websocket(self):
        """웹소켓 연결을 비동기로 종료합니다."""
        return await self._broker_api_wrapper.disconnect_websocket()

    async def subscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가)를 구독합니다."""
        return await self._broker_api_wrapper.subscribe_realtime_price(stock_code)

    async def unsubscribe_realtime_price(self, stock_code):
        """실시간 주식체결 데이터(현재가) 구독을 해지합니다."""
        return await self._broker_api_wrapper.unsubscribe_realtime_price(stock_code)

    async def subscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터를 구독합니다."""
        return await self._broker_api_wrapper.subscribe_realtime_quote(stock_code)

    async def unsubscribe_realtime_quote(self, stock_code):
        """실시간 주식호가 데이터 구독을 해지합니다."""
        return await self._broker_api_wrapper.unsubscribe_realtime_quote(stock_code)

    async def get_current_stock_price(self, stock_code) -> ResCommonResponse:
        self._logger.info(f"Service - {stock_code} 현재가 조회 요청")
        return await self._broker_api_wrapper.get_current_price(stock_code)

    async def get_account_balance(self) -> ResCommonResponse:
        self._logger.info(f"Service - 계좌 잔고 조회 요청 (환경: {'모의투자' if self._env.is_paper_trading else '실전'})")
        if self._env.is_paper_trading:
            return await self._broker_api_wrapper.get_account_balance()
        else:
            return await self._broker_api_wrapper.get_real_account_balance()

    async def place_buy_order(self, stock_code, price, qty, order_dvsn) -> ResCommonResponse:
        self._logger.info(
            f"Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )

        try:
            response_common : ResCommonResponse = await self._broker_api_wrapper.place_stock_order(
                stock_code=stock_code,
                order_price=price,
                order_qty=qty,
                trade_type="buy",
                order_dvsn=order_dvsn
            )
        except Exception as e:
            self._logger.error(f"Service - 매수 주문 중 오류 발생: {str(e)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value, # Enum 값 사용
                msg1=f"매수 주문 처리 중 예외 발생: {str(e)}",
                data=None
            )

        if response_common.rt_cd != ErrorCode.SUCCESS.value: # Enum 값 사용
            msg = getattr(response_common, "msg1", "매수 주문 실패")  # 방어적 접근
            self._logger.error(f"매수 주문 실패: {msg}")
            return response_common

        return response_common

    async def place_sell_order(self, stock_code, price, qty, order_dvsn) -> ResCommonResponse:
        self._logger.info(
            f"Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )

        try:
            response_common : ResCommonResponse = await self._broker_api_wrapper.place_stock_order(
                stock_code=stock_code,
                order_price=price,
                order_qty=qty,
                trade_type="sell",
                order_dvsn=order_dvsn
            )
        except Exception as e:
            self._logger.error(f"Service - 매도 주문 중 오류 발생: {str(e)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value, # Enum 값 사용
                msg1=f"매도 주문 처리 중 예외 발생: {str(e)}",
                data=None
            )

        if response_common.rt_cd != ErrorCode.SUCCESS.value: # Enum 값 사용
            msg = getattr(response_common, "msg1", "매도 주문 실패")
            self._logger.error(f"매도 주문 실패: {msg}")
            return response_common

        return response_common

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = None) -> ResCommonResponse:
        """
        시가총액 상위 종목을 조회하고 결과를 반환합니다 (모의투자 미지원).
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResTopMarketCapApiItem] 포함.
        """
        if count is None:
            count = 10
            self._logger.warning(f"[경고] count 파라미터가 명시되지 않아 기본값 10을 사용합니다. market_code={market_code}")

        self._logger.info(f"Service - 시가총액 상위 종목 조회 요청 - 시장: {market_code}, 개수: {count}")

        if self._env.is_paper_trading:
            self._logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1="모의투자 미지원 API입니다.", data=[]) # Enum 값 사용

        return await self._broker_api_wrapper.get_top_market_cap_stocks_code(market_code, count)

    async def get_top_10_market_cap_stocks_with_prices(self) -> ResCommonResponse:
        """
        시가총액 1~10위 종목의 현재가를 조회합니다.
        시장 개장 여부를 확인하고, 모의투자 미지원 API입니다.
        이제 시장 개장까지 기다리지 않고, 닫혀있으면 바로 None을 반환합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResMarketCapStockItem] 포함.
        """
        self._logger.info("Service - 시가총액 1~10위 종목 현재가 조회 요청")

        if self._time_manager and not self._time_manager.is_market_open():
            self._logger.warning("시장이 닫혀 있어 시가총액 1~10위 종목 현재가 조회를 수행할 수 없습니다.")
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value, # Enum 값 사용
                msg1="시장이 닫혀 있어 조회 불가",
                data=[]
            )

        if self._env.is_paper_trading:
            self._logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1="모의투자 미지원 API입니다.", data=[]) # Enum 값 사용

        top_stocks_response_common: ResCommonResponse = await self.get_top_market_cap_stocks_code("0000")
        if top_stocks_response_common.rt_cd != ErrorCode.SUCCESS.value: # Enum 값 사용
            self._logger.error(f"시가총액 상위 종목 조회 실패: {top_stocks_response_common.msg1}")
            return top_stocks_response_common

        top_stocks_list: List[ResTopMarketCapApiItem] = top_stocks_response_common.data
        if not top_stocks_list:
            self._logger.info("시가총액 상위 종목 목록을 찾을 수 없습니다.")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value, # Enum 값 사용
                msg1="시가총액 1~10위 종목 현재가 조회 결과 없음",
                data=[]
            )

        results: List[ResMarketCapStockItem] = []
        for i, stock_info_api_item in enumerate(top_stocks_list):
            if i >= 10:
                break

            stock_code = stock_info_api_item.mksc_shrn_iscd
            stock_name = stock_info_api_item.hts_kor_isnm or "N/A"
            stock_rank = stock_info_api_item.data_rank or "N/A"

            if stock_code:
                current_price_response_common: ResCommonResponse = await self.get_current_stock_price(stock_code)
                if current_price_response_common.rt_cd == ErrorCode.SUCCESS.value: # Enum 값 사용
                    current_price_output_data = current_price_response_common.data
                    current_price = current_price_output_data.get('output').get('stck_prpr', 'N/A')
                    results.append(ResMarketCapStockItem(
                        rank=stock_rank,
                        name=stock_name,
                        code=stock_code,
                        current_price=current_price
                    ))
                    self._logger.debug(f"종목 {stock_code} ({stock_name}) 현재가 {current_price} 조회 성공.")
                else:
                    self._logger.error(f"종목 {stock_code} ({stock_name}) 현재가 조회 실패: {current_price_response_common.msg1}")
            else:
                self._logger.warning(f"시가총액 상위 종목 목록에서 유효한 종목코드를 찾을 수 없습니다: {stock_info_api_item}")

        if results:
            self._logger.info("시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, # Enum 값 사용
                msg1="시가총액 1~10위 종목 현재가 조회 성공",
                data=results
            )
        else:
            self._logger.warning("시가총액 1~10위 종목 현재가 조회 결과 없음.")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value, # Enum 값 사용
                msg1="시가총액 1~10위 종목 현재가 조회 결과 없음",
                data=[]
            )

    async def get_yesterday_upper_limit_stocks(self, stock_codes: List[str]) -> ResCommonResponse:
        """
        전체 종목 리스트 중 어제 상한가에 도달한 종목을 필터링합니다. (TODO: 재검증 및 TC 추가 필요)
        ResCommonResponse 형태로 반환하며, data 필드에 List[Dict] 포함.
        """
        results = []

        for code in stock_codes:
            try:
                price_info_common: ResCommonResponse = await self._broker_api_wrapper.get_price_summary(code)
                if price_info_common.rt_cd != ErrorCode.SUCCESS.value: # Enum 값 사용
                    self._logger.warning(f"{code} 종목 가격 요약 정보 조회 실패: {price_info_common.msg1}. 필터링 제외.")
                    continue

                price_info: ResPriceSummary = price_info_common.data
                if price_info is None:
                    self._logger.warning(f"{code} 종목 가격 요약 정보 데이터가 None입니다. 필터링 제외.")
                    continue


                current_price = price_info.current
                prdy_ctrt = price_info.prdy_ctrt

                if prdy_ctrt >= 29.0: # 29% 이상이면 상한가 근접으로 판단
                    results.append({
                        "code": code,
                        "price": current_price,
                        "change_rate": prdy_ctrt
                    })
            except Exception as e:
                self._logger.warning(f"{code} 상한가 필터링 중 오류: {e}")
                continue #

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, # Enum 값 사용
            msg1="어제 상한가 종목 필터링 성공 (임시 로직)",
            data=results
        )

    async def get_current_upper_limit_stocks(self, stock_codes: List[str]) -> ResCommonResponse:
        """
        전체 종목 리스트 중 현재 상한가에 도달한 종목을 필터링합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[Dict] (종목 정보) 포함.
        """
        results: List[ResBasicStockInfo] = []
        total_stocks = len(stock_codes)
        progress_step = max(1, total_stocks // 10)

        for idx, code in enumerate(stock_codes):
            if (idx + 1) % progress_step == 0 or (idx + 1) == total_stocks:
                current_percentage = ((idx + 1) / total_stocks) * 100 if total_stocks > 0 else 100
                print(f"\r처리 중... {current_percentage:.0f}% 완료 ({idx + 1}/{total_stocks})", end="", flush=True)

            try:
                price_info_common: ResCommonResponse = await self._broker_api_wrapper.get_price_summary(code)
                if price_info_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
                    self._logger.warning(f"{code} 종목 가격 요약 정보 조회 실패: {price_info_common.msg1}. 필터링 제외.")
                    continue

                price_info: ResPriceSummary = price_info_common.data
                if price_info is None:
                    self._logger.warning(f"{code} 종목 가격 요약 정보 데이터가 None입니다. 필터링 제외.")
                    continue

                current_price = price_info.current
                open_price = price_info.open
                prdy_ctrt = price_info.prdy_ctrt

                name_response_common = await self._broker_api_wrapper.get_name_by_code(code)
                name = name_response_common  # get_name_by_code는 현재 문자열을 직접 반환

                if prdy_ctrt > 29.0:  # 등락률 조건 (전일 대비 29% 이상을 상한가로 간주)
                    stock_info = ResBasicStockInfo(
                        code=code,
                        name=name,
                        open_price=open_price,
                        current_price=current_price,
                        change_rate=price_info.change_rate,
                        prdy_ctrt=prdy_ctrt
                    )
                    results.append(stock_info)
            except Exception as e:
                self._logger.warning(f"{code} 현재 상한가 필터링 중 오류: {e}")
                continue  #

        print("\r" + " " * 80 + "\r", end="", flush=True)
        self._time_manager.get_current_kst_time()

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="현재 상한가 종목 필터링 성공",
            data=results  # List[ResBasicStockInfo]
        )

    # `get_current_upper_limit_stocks` 메서드가 정의된 클래스 내부에 다음 헬퍼 메서드를 추가합니다.
    async def _fetch_stock_data(self, code: str):
        """
        단일 종목에 대해 get_price_summary와 get_name_by_code를 비동기적으로 조회하는 헬퍼 메서드.
        """
        price_info = await self._broker_api_wrapper.get_price_summary(code)
        name = await self._broker_api_wrapper.get_name_by_code(code)
        print(f"\rFetch_StockDtata.. {price_info}% 완료 ({name})")

        return price_info, name

    async def get_all_stocks_code(self) -> ResCommonResponse:
        """
        전체 종목 코드를 조회합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[str] (종목 코드 리스트) 포함.
        """
        self._logger.info("Service - 전체 종목 코드 조회 요청")

        try:
            codes = await self._broker_api_wrapper.get_all_stock_code_list() # 현재 List[str] 반환

            if not isinstance(codes, list):
                msg = f"비정상 응답 형식: {codes}"
                self._logger.warning(msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.PARSING_ERROR.value, # Enum 값 사용
                    msg1=msg,
                    data=[]
                )

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value, # Enum 값 사용
                msg1="전체 종목 코드 조회 성공",
                data=codes
            )

        except Exception as e:
            error_msg = f"전체 종목 코드 조회 실패: {e}"
            self._logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value, # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

    async def get_asking_price(self, stock_code: str) -> ResCommonResponse:
        """종목의 실시간 호가 정보를 조회합니다."""
        self._logger.info(f"Service - {stock_code} 종목 호가 정보 조회 요청")
        return await self._broker_api_wrapper.get_asking_price(stock_code)

    async def get_time_concluded_prices(self, stock_code: str) -> ResCommonResponse:
        """종목의 시간대별 체결가 정보를 조회합니다."""
        self._logger.info(f"Service - {stock_code} 종목 시간대별 체결가 조회 요청")
        return await self._broker_api_wrapper.get_time_concluded_prices(stock_code)

    async def search_stocks_by_keyword(self, keyword: str) -> ResCommonResponse:
        """키워드로 종목을 검색합니다."""
        self._logger.info(f"Service - '{keyword}' 키워드로 종목 검색 요청")
        return await self._broker_api_wrapper.search_stocks_by_keyword(keyword)

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """상승률 또는 하락률 상위 종목을 조회합니다."""
        direction = "상승" if rise else "하락"
        self._logger.info(f"Service - {direction}률 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """거래량 상위 종목을 조회합니다."""
        self._logger.info("Service - 거래량 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_volume_stocks()

    async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
        """외국인 순매수 상위 종목을 조회합니다."""
        self._logger.info("Service - 외국인 순매수 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_foreign_buying_stocks()

    async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
        """특정 종목의 뉴스를 조회합니다."""
        self._logger.info(f"Service - {stock_code} 종목 뉴스 조회 요청")
        return await self._broker_api_wrapper.get_stock_news(stock_code)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """특정 ETF의 상세 정보를 조회합니다."""
        self._logger.info(f"Service - {etf_code} ETF 정보 조회 요청")
        return await self._broker_api_wrapper.get_etf_info(etf_code)

    async def handle_realtime_stream(self, stock_codes: list[str], fields: list[str], duration: int = 30):
        """
        실시간 데이터 스트림을 구독하고 지정된 시간 동안 수신합니다.

        :param stock_codes: 종목 코드 리스트
        :param fields: 수신할 실시간 데이터 필드 (e.g. ["price", "quote"])
        :param duration: 구독 유지 시간 (초), 기본 30초
        """
        self._logger.info(f"실시간 스트림 시작 - 종목: {stock_codes}, 필드: {fields}, 시간: {duration}s")

        try:
            await self.connect_websocket()

            for code in stock_codes:
                if "price" in fields:
                    await self.subscribe_realtime_price(code)
                if "quote" in fields:
                    await self.subscribe_realtime_quote(code)

            start_time = datetime.now()
            while (datetime.now() - start_time) < timedelta(seconds=duration):
                await asyncio.sleep(1)  # 단순 대기 (메시지는 내부 handler에서 자동 처리됨)

        except Exception as e:
            self._logger.error(f"실시간 스트림 처리 중 오류 발생: {str(e)}")

        finally:
            for code in stock_codes:
                if "price" in fields:
                    await self.unsubscribe_realtime_price(code)
                if "quote" in fields:
                    await self.unsubscribe_realtime_quote(code)

            await self.disconnect_websocket()
            self._logger.info("실시간 스트림 종료")