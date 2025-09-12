# services/trading_service.py
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from core.time_manager import TimeManager
import logging
from brokers.broker_api_wrapper import BrokerAPIWrapper
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from config.DynamicConfig import DynamicConfig

# common/types에서 모든 ResTypedDict와 ErrorCode 임포트
from common.types import (
    ResPriceSummary, ResCommonResponse, ErrorCode,
    ResTopMarketCapApiItem, ResBasicStockInfo, ResFluctuation,ResDailyChartApiItem
)


class TradingService:
    """
    한국투자증권 Open API와 관련된 핵심 비즈니스 로직을 제공하는 서비스 계층입니다.
    이 클래스의 메서드는 UI와 독립적으로 데이터를 조회하고 처리하며, 결과를 반환합니다.
    """

    def __init__(self, broker_api_wrapper: BrokerAPIWrapper, env: KoreaInvestApiEnv, logger=None,
                 time_manager: TimeManager = None):
        self._broker_api_wrapper = broker_api_wrapper
        self._env = env
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

        elif data.get('type') == 'realtime_program_trading':
            d = data.get('data', {})
            t = d.get('STCK_CNTG_HOUR', 'N/A')
            ntby = d.get('NTBY_TR_PBMN', '0')
            msg = f"[프로그램매매 - {t}] 순매수거래대금: {ntby}"
            print(f"\r{msg}{' ' * max(0, 80 - len(msg))}", end="")
            self._logger.info(msg)

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

    async def subscribe_program_trading(self, stock_code: str):
        return await self._broker_api_wrapper.subscribe_program_trading(stock_code)

    async def unsubscribe_program_trading(self, stock_code: str):
        return await self._broker_api_wrapper.unsubscribe_program_trading(stock_code)

    async def get_price_summary(self, stock_code) -> ResCommonResponse:
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다 (KoreaInvestApiQuotations 위임)."""
        self._logger.info(f"Service - {stock_code} 종목 요약 정보 조회 요청")
        return await self._broker_api_wrapper.get_price_summary(stock_code)

    async def get_current_stock_price(self, stock_code) -> ResCommonResponse:
        self._logger.info(f"Trading_Service - {stock_code} 현재가 조회 요청")
        return await self._broker_api_wrapper.get_current_price(stock_code)

    async def get_account_balance(self) -> ResCommonResponse:
        return await self._broker_api_wrapper.get_account_balance()

    async def place_buy_order(self, stock_code, price, qty) -> ResCommonResponse:
        self._logger.info(
            f"Service - 주식 매수 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )

        try:
            response_common: ResCommonResponse = await self._broker_api_wrapper.place_stock_order(
                stock_code=stock_code,
                order_price=price,
                order_qty=qty,
                is_buy=True
            )
        except Exception as e:
            self._logger.error(f"Service - 매수 주문 중 오류 발생: {str(e)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,  # Enum 값 사용
                msg1=f"매수 주문 처리 중 예외 발생: {str(e)}",
                data=None
            )

        if response_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
            msg = getattr(response_common, "msg1", "매수 주문 실패")  # 방어적 접근
            self._logger.error(f"매수 주문 실패: {msg}")
            return response_common

        return response_common

    async def place_sell_order(self, stock_code, price, qty) -> ResCommonResponse:
        self._logger.info(
            f"Service - 주식 매도 주문 요청 - 종목: {stock_code}, 수량: {qty}, 가격: {price}"
        )

        try:
            response_common: ResCommonResponse = await self._broker_api_wrapper.place_stock_order(
                stock_code=stock_code,
                order_price=price,
                order_qty=qty,
                is_buy=False
            )
        except Exception as e:
            self._logger.error(f"Service - 매도 주문 중 오류 발생: {str(e)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,  # Enum 값 사용
                msg1=f"매도 주문 처리 중 예외 발생: {str(e)}",
                data=None
            )

        if response_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
            msg = getattr(response_common, "msg1", "매도 주문 실패")
            self._logger.error(f"매도 주문 실패: {msg}")
            return response_common

        return response_common

    async def get_top_market_cap_stocks_code(self, market_code: str, limit: int = None) -> ResCommonResponse:
        """
        시가총액 상위 종목을 조회하고 결과를 반환합니다 (모의투자 미지원).
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResTopMarketCapApiItem] 포함.
        """
        if limit is None:
            limit = 30
            self._logger.warning(f"[경고] count 파라미터가 명시되지 않아 기본값 30을 사용합니다. market_code={market_code}")

        self._logger.info(f"Service - 시가총액 상위 종목 조회 요청 - 시장: {market_code}, 개수: {limit}")

        if self._env.is_paper_trading:
            self._logger.warning("Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1="모의투자 미지원 API입니다.", data=[])  # Enum 값 사용

        return await self._broker_api_wrapper.get_top_market_cap_stocks_code(market_code, limit)

    async def get_current_upper_limit_stocks(self, rise_stocks: List) -> ResCommonResponse:
        """
        전체 종목 리스트 중 현재 상한가에 도달한 종목을 필터링합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[Dict] (종목 정보) 포함.
        """
        results: List[ResBasicStockInfo] = []

        for stock_info in rise_stocks:
            try:
                fluctuation_info: ResFluctuation = stock_info
                code = fluctuation_info.stck_shrn_iscd
                current_price = int(fluctuation_info.stck_prpr)
                prdy_ctrt = float(fluctuation_info.prdy_ctrt)
                name = fluctuation_info.hts_kor_isnm
                change_rate = float(fluctuation_info.prdy_vrss)

                if prdy_ctrt > 29.0:  # 등락률 조건 (전일 대비 29% 이상을 상한가로 간주)
                    stock_info = ResBasicStockInfo(
                        code=code,
                        name=name,
                        current_price=current_price,
                        change_rate=change_rate,
                        prdy_ctrt=prdy_ctrt
                    )
                    results.append(stock_info)
            except Exception as e:
                self._logger.warning(f"{code} 현재 상한가 필터링 중 오류: {e}")
                continue  #

        self._time_manager.get_current_kst_time()

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="현재 상한가 종목 필터링 성공",
            data=results  # List[ResBasicStockInfo]
        )

    async def get_all_stocks_code(self) -> ResCommonResponse:
        """
        전체 종목 코드를 조회합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[str] (종목 코드 리스트) 포함.
        """
        self._logger.info("Service - 전체 종목 코드 조회 요청")

        try:
            codes = await self._broker_api_wrapper.get_all_stock_code_list()  # 현재 List[str] 반환

            if not isinstance(codes, list):
                msg = f"비정상 응답 형식: {codes}"
                self._logger.warning(msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                    msg1=msg,
                    data=[]
                )

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
                msg1="전체 종목 코드 조회 성공",
                data=codes
            )

        except Exception as e:
            error_msg = f"전체 종목 코드 조회 실패: {e}"
            self._logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,  # Enum 값 사용
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

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str,
                                           fid_period_div_code: str = 'D') -> ResCommonResponse:
        """일별/분봉 주식 시세 차트 데이터를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        self._logger.info(f"Service - {stock_code} 종목 일별/분봉 주식 시세 차트 데이터를 조회 요청")
        return await self._broker_api_wrapper.inquire_daily_itemchartprice(stock_code=stock_code, date=date,
                                                                           fid_period_div_code=fid_period_div_code)

    # async def search_stocks_by_keyword(self, keyword: str) -> ResCommonResponse:
    #     """키워드로 종목을 검색합니다."""
    #     self._logger.info(f"Service - '{keyword}' 키워드로 종목 검색 요청")
    #     return await self._broker_api_wrapper.search_stocks_by_keyword(keyword)

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """상승률 또는 하락률 상위 종목을 조회합니다."""
        direction = "상승" if rise else "하락"
        self._logger.info(f"Service - {direction}률 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """거래량 상위 종목을 조회합니다."""
        self._logger.info("Service - 거래량 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_volume_stocks()

    async def get_top_trading_value_stocks(self) -> ResCommonResponse:
        """
        거래대금 상위 종목 조회.
        거래량/시가총액/상승률/하락률 4개 기존 API 결과를 병합하여
        acml_tr_pbmn(거래대금) 기준 상위 30개를 반환한다.
        """
        self._logger.info("Service - 거래대금 상위 종목 조회 요청 (4개 소스 병합)")

        # 기존 메서드 호출 (각각 캐싱 적용됨)
        vol_resp = await self._broker_api_wrapper.get_top_volume_stocks()
        mc_resp = await self._broker_api_wrapper.get_top_market_cap_stocks_code("J", 30)
        rise_resp = await self._broker_api_wrapper.get_top_rise_fall_stocks(True)
        fall_resp = await self._broker_api_wrapper.get_top_rise_fall_stocks(False)

        # 거래량 데이터 (acml_tr_pbmn 포함, dict 리스트)
        volume_stocks = vol_resp.data if vol_resp and vol_resp.rt_cd == ErrorCode.SUCCESS.value else []
        if isinstance(volume_stocks, dict):
            volume_stocks = volume_stocks.get("output", [])

        # 시가총액 데이터 (dict 또는 dataclass 리스트)
        mc_stocks = mc_resp.data if mc_resp and mc_resp.rt_cd == ErrorCode.SUCCESS.value else []

        # 상승률/하락률 데이터 (ResFluctuation dataclass 리스트)
        rise_stocks = rise_resp.data if rise_resp and rise_resp.rt_cd == ErrorCode.SUCCESS.value else []
        fall_stocks = fall_resp.data if fall_resp and fall_resp.rt_cd == ErrorCode.SUCCESS.value else []

        def _to_dict(item):
            return item.to_dict() if hasattr(item, 'to_dict') else (item if isinstance(item, dict) else {})

        def _get_code(d):
            return d.get("mksc_shrn_iscd") or d.get("stck_shrn_iscd") or d.get("iscd") or ""

        # 종목코드 기준 병합 (거래량 데이터 우선 — acml_tr_pbmn 정확)
        merged = {}
        for stock in volume_stocks:
            d = _to_dict(stock)
            code = _get_code(d)
            if code:
                merged[code] = d

        for stock in list(mc_stocks or []) + list(rise_stocks or []) + list(fall_stocks or []):
            d = _to_dict(stock)
            code = _get_code(d)
            if code and code not in merged:
                if not d.get("acml_tr_pbmn"):
                    try:
                        price = int(d.get("stck_prpr", "0") or "0")
                        vol = int(d.get("acml_vol", "0") or "0")
                        d["acml_tr_pbmn"] = str(price * vol)
                    except (ValueError, TypeError):
                        d["acml_tr_pbmn"] = "0"
                merged[code] = d

        # 거래대금 기준 내림차순 정렬, 상위 30개
        result = list(merged.values())
        result.sort(key=lambda x: int(x.get("acml_tr_pbmn", "0") or "0"), reverse=True)
        result = result[:30]
        for i, stock in enumerate(result, 1):
            stock["data_rank"] = str(i)

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="거래대금 상위 종목 조회 성공",
            data=result
        )

    # async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
    #     """외국인 순매수 상위 종목을 조회합니다."""
    #     self._logger.info("Service - 외국인 순매수 상위 종목 조회 요청")
    #     return await self._broker_api_wrapper.get_top_foreign_buying_stocks()
    #
    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """특정 종목의 뉴스를 조회합니다."""
    #     self._logger.info(f"Service - {stock_code} 종목 뉴스 조회 요청")
    #     return await self._broker_api_wrapper.get_stock_news(stock_code)

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
                await self._time_manager.sleep(1)  # 단순 대기 (메시지는 내부 handler에서 자동 처리됨)

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

    def _normalize_ohlcv_rows(self, items: List[Any]) -> List[dict]:
        """
        한국투자 일봉 응답(ResDailyChartApiItem list) 또는 dict list를
        표준 OHLCV 스키마로 정규화한다.
          반환: [{"date":"YYYYMMDD","open":float,"high":float,"low":float,"close":float,"volume":int}, ...]
        """

        def _get(it, attr, default=None):
            if it is None:
                return default
            if isinstance(it, dict):
                return it.get(attr, default)
            # dataclass/object 속성 접근
            return getattr(it, attr, default)

        def _to_float(x):
            try:
                return float(str(x).replace(",", ""))
            except Exception:
                return None

        def _to_int(x):
            try:
                return int(float(str(x).replace(",", "")))
            except Exception:
                return None

        rows = []
        for it in items or []:
            # ResDailyChartApiItem 필드 우선, 없으면 표준키 사용
            date = _get(it, "stck_bsop_date") or _get(it, "date")
            open_ = _get(it, "stck_oprc") or _get(it, "open")
            high = _get(it, "stck_hgpr") or _get(it, "high")
            low = _get(it, "stck_lwpr") or _get(it, "low")
            close = _get(it, "stck_clpr") or _get(it, "close")
            volume = _get(it, "acml_vol") or _get(it, "volume")

            if not date:
                continue

            rows.append({
                "date": date,
                "open": _to_float(open_),
                "high": _to_float(high),
                "low": _to_float(low),
                "close": _to_float(close),
                "volume": _to_int(volume),
            })

        # 날짜 오름차순 정렬 + 날짜 없는 행 제거는 위에서 처리
        rows.sort(key=lambda r: r["date"])
        return rows

    def _calc_range_by_period(self, period: str, end_dt: datetime | None, limit: int | None = None) -> tuple[str, str]:
        """
        period: 'D'|'W'|'M'
        end_dt: 기준일(없으면 now KST)
        limit : 원하는 봉 개수(없으면 합리적 기본값)
        return: (start_yyyymmdd, end_yyyymmdd)
        """
        if end_dt is None:
            # KST 기준 현재
            end_dt = self._time_manager.get_current_kst_time()

        period = (period or "D").upper()
        if period == "D":
            # 일봉: 최소 240일(약 1년) 확보. limit 있으면 2배 버퍼.
            days = max((limit or 120) * 2, 240)
            start_dt = end_dt - timedelta(days=days)

        elif period == "W":
            # 주봉: 최소 104주(약 2년) 확보. limit 있으면 2배 버퍼.
            weeks = max((limit or 52) * 2, 104)
            start_dt = end_dt - timedelta(weeks=weeks)

        elif period == "M":
            # 월봉: 개략치로 31일 기준 산정(외부 lib 없이). 최소 60개월(5년).
            months = max((limit or 24) * 2, 60)
            days = months * 31
            start_dt = end_dt - timedelta(days=days)
        else:
            # 알 수 없는 값 → 일봉처럼 처리
            days = max((limit or 120) * 2, 240)
            start_dt = end_dt - timedelta(days=days)

        return self._time_manager.to_yyyymmdd(start_dt), self._time_manager.to_yyyymmdd(end_dt)

    async def get_ohlcv(
            self,
            stock_code: str,
            period: str = "D",
    ) -> ResCommonResponse:
        """
        시작일~종료일 범위형 차트 API 호출 (일/분 공통).
        """
        start_yyyymmdd, end_yyyymmdd = self._calc_range_by_period(
            period=period,
            end_dt=self._time_manager.get_current_kst_time() if hasattr(self, "_time_manager") else datetime.now(),
            limit=DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE  # 사용자가 입력한 봉 개수 있으면 넘기고, 없으면 None
        )

        raw = await self._broker_api_wrapper.inquire_daily_itemchartprice(
            stock_code=stock_code,
            start_date=start_yyyymmdd,
            end_date=end_yyyymmdd,
            fid_period_div_code=(period or "D").upper(),
        )
        if not raw or raw.rt_cd != ErrorCode.SUCCESS.value:
            return raw or ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="차트 API 실패", data=[])

        rows = self._normalize_ohlcv_rows(raw.data)
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=f"OHLCV {len(rows)}건", data=rows)

    async def get_ohlcv_range(
            self,
            stock_code: str,
            period: str = "D",
            start_date: Optional[str] = None,  # YYYYMMDD
            end_date: Optional[str] = None,  # YYYYMMDD
    ) -> ResCommonResponse:
        """
        시작일~종료일 범위형 차트 API 호출 (일/분 공통).
        """
        ed = end_date or self._time_manager.get_current_kst_time()
        # start_date가 없다면 달력 기준 넉넉한 버퍼(예: 240일)로 설정
        sd = start_date or (datetime.strptime(ed, "%Y%m%d") - timedelta(days=240)).strftime("%Y%m%d")

        raw = await self._broker_api_wrapper.inquire_daily_itemchartprice(
            stock_code=stock_code,
            start_date=sd,
            end_date=ed,
            fid_period_div_code=(period or "D").upper(),
        )
        if not raw or raw.rt_cd != ErrorCode.SUCCESS.value:
            return raw or ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="차트 API 실패", data=[])

        rows = self._normalize_ohlcv_rows(raw.data)
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=f"OHLCV {len(rows)}건", data=rows)

    async def get_recent_daily_ohlcv(
            self,
            code: str,
            limit: int = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE,
            end_date: Optional[str] = None,
            start_date: Optional[str] = None,  # (옵션) 달력기준 시작일 강제 시 사용
    ) -> List[Dict[str, Any]]:
        """
        최근 'limit'개 *거래일* 일봉을 반환.
        API는 시작/종료일 범위를 요구하므로 넉넉한 범위로 받아서 슬라이스로 120개 보장.
        """
        ed = self._time_manager.to_yyyymmdd(end_date)
        # start_date 없으면 넉넉히 과거로 (달력 240일)
        sd = self._time_manager.to_yyyymmdd(start_date) if start_date else (
            (datetime.strptime(ed, "%Y%m%d") - timedelta(days=240)).strftime("%Y%m%d")
        )

        resp = await self.get_ohlcv_range(code, period="D", start_date=sd, end_date=ed)
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            return []

        rows = resp.data or []

        # 2) 모자라면(예: 긴 휴장 구간) 과거로 더 확장해서 한 번 더 시도
        attempts = 0
        while len(rows) < limit and attempts < 2:
            # 더 과거로 240일 확장
            new_ed = (datetime.strptime(sd, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            new_sd = (datetime.strptime(new_ed, "%Y%m%d") - timedelta(days=max(limit * 2, 240))).strftime("%Y%m%d")
            more = await self.get_ohlcv_range(code, period="D", start_date=new_sd, end_date=new_ed)
            if more and more.rt_cd == ErrorCode.SUCCESS.value and more.data:
                rows = (more.data or []) + rows
            sd = new_sd
            attempts += 1

        # 3) 최근 120개 슬라이스(거래일 기준 보장)
        rows = rows[-limit:] if limit and len(rows) > limit else rows
        return rows

    async def get_intraday_minutes_today(self, *, stock_code: str, input_hour_1: str) -> ResCommonResponse:
        """
        URL: /quotations/inquire-time-itemchartprice
        TR : FHKST03010200 (모의/실전 공통)
        """
        # 기본값들(필요시 조정): UN=통합, 과거데이터 포함, 기타구분 "0"
        return await self._broker_api_wrapper.inquire_time_itemchartprice(
            stock_code=stock_code,
            input_hour_1=input_hour_1,
            pw_data_incu_yn="Y",
            etc_cls_code="0",
        )

    async def get_intraday_minutes_by_date(
        self, *, stock_code: str, input_date_1: str, input_hour_1: str = ""
    ) -> ResCommonResponse:
        """
        URL: /quotations/inquire-time-dailychartprice
        TR : FHKST03010230 (실전만)
        """
        if self._env.is_paper_trading:
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="일별 분봉(inquire-time-dailychartprice)은 모의투자 미지원입니다.",
                data=[]
            )

        # 허봉 포함은 '공백 필수' 스펙
        return await self._broker_api_wrapper.inquire_time_dailychartprice(
            stock_code=stock_code,
            input_date_1=input_date_1,
            input_hour_1=input_hour_1,
            pw_data_incu_yn="Y",
            fake_tick_incu_yn="",   # 공백 필수
        )
