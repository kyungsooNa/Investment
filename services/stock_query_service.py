# app/stock_query_service.py
from __future__ import annotations
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResBasicStockInfo, \
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
        self.logger.info(f"Stock_Query_Service - {stock_code} 현재가 조회 요청")
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

    async def handle_get_top_market_cap_stocks_code(self, market_code: str = "0000", limit: int = 30) -> ResCommonResponse:
        """
        시가총액 상위 종목 중 상한가 도달 종목 조회 (출력 X).
        data: List[dict(code,name,price,change_rate)]
        """
        self.logger.debug(f"상한가 스캔 요청 (시장={market_code}, limit={limit})")

        # 모의투자 / 장시간 검증
        if getattr(self.trading_service._env, "is_paper_trading", False):
            self.logger.warning("모의투자 환경에서는 상한가 조회 미지원")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="모의투자 미지원 API입니다.",
                data=None
            )

        try:
            # 상위 종목 조회
            top_res: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code, limit)
            if not top_res or top_res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"상위 종목 목록 조회 실패: {top_res}")
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="상위 종목 목록 조회 실패",
                    data=None
                )

            top_list: List[ResTopMarketCapApiItem] = top_res.data or []
            if not top_list:
                self.logger.debug("상위 종목 없음")
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="조회 성공 (종목 없음)",
                    data=[]
                )

            targets = top_list[:limit]
            found: list[dict] = []

            for item in targets:
                # dataclass(ResTopMarketCapApiItem)와 dict 모두 지원
                get = (lambda k: getattr(item, k, None)) if not isinstance(item, dict) else item.get

                code = get("mksc_shrn_iscd") or get("iscd")
                name = get("hts_kor_isnm")
                prdy_vrss_sign = get("prdy_vrss_sign")
                stck_prpr = get("stck_prpr")
                prdy_ctrt = get("prdy_ctrt")

                if not code:
                    self.logger.warning(f"유효하지 않은 종목코드: {item}")
                    continue

                # 정책: prdy_vrss_sign == '1'이면 상한으로 간주
                if prdy_vrss_sign == "1":
                    found.append({
                        "code": code,
                        "name": name,
                        "price": str(stck_prpr) if stck_prpr is not None else None,
                        "change_rate": str(prdy_ctrt) if prdy_ctrt is not None else None,
                    })
                    self.logger.debug(f"상한가 발견: {name}({code}) {stck_prpr}원 {prdy_ctrt}%")
                else:
                    # 필요시 디버그 로그만
                    self.logger.debug(f"상한가 아님: {name}({code}) sign={prdy_vrss_sign}")

            self.logger.info("시가총액 상위 종목 조회 성공")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="조회 성공",
                data=found  # 빈 리스트 허용
            )

        except Exception as e:
                self.logger.exception("상한가 조회 중 예외")
                return ResCommonResponse(
                    rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                    msg1=f"예외 발생: {e}",
                    data=None
                )

    async def get_stock_change_rate(self, stock_code: str) -> ResCommonResponse:
        """
        전일대비 등락률 조회. 출력 없음. 계산/포맷만 수행하여 ResCommonResponse로 반환.
        data 예시:
          {
            "stock_code": "005930",
            "current_price": "70400",
            "change_value_display": "+500",   # 부호/0 처리 적용된 표시값
            "change_rate": "0.71"             # API 그대로 문자열 유지
          }
        """
        res: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)
        if not (res and res.rt_cd == ErrorCode.SUCCESS.value):
            self.logger.error(f"{stock_code} 전일대비 등락률 조회 실패: {res}")
            # 실패도 통일된 형태로 반환
            return ResCommonResponse(rt_cd="1", msg1="조회 실패", data={"stock_code": stock_code})

        output = res.data.get("output") or {}
        current_price = output.stck_prpr
        change_val_str = output.prdy_vrss
        change_sign_code = output.prdy_vrss_sign
        change_rate_str = output.prdy_ctrt

        actual_sign = self._get_sign_from_code(change_sign_code)

        display_change_val = change_val_str
        try:
            f = float(change_val_str)
            if f > 0:
                display_change_val = f"{actual_sign}{change_val_str}"
            elif f == 0:
                display_change_val = "0"
        except (ValueError, TypeError):
            # 숫자 아님 → 그대로 노출
            pass

        data = {
            "stock_code": stock_code,
            "current_price": current_price,
            "change_value_display": display_change_val,
            "change_rate": change_rate_str,
        }
        self.logger.info(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가={current_price}, "
            f"전일대비={display_change_val}, 등락률={change_rate_str}%"
        )
        return ResCommonResponse(rt_cd="0", msg1="정상", data=data)

    async def get_open_vs_current(self, stock_code: str) -> ResCommonResponse:
        """
        시가 대비 등락률/금액 계산 후 반환. 출력 없음.
        data 예시:
          {
            "stock_code": "005930",
            "current_price": "70400",
            "open_price": "70000",
            "vs_open_value_display": "+400",   # 금액 부호/0 처리
            "vs_open_rate_display": "+0.57%"   # 퍼센트 부호/0 처리
          }
        """
        res: ResCommonResponse = await self.trading_service.get_current_stock_price(stock_code)
        if not (res and res.rt_cd == ErrorCode.SUCCESS.value):
            self.logger.error(f"{stock_code} 시가대비 조회 실패: {res}")
            return ResCommonResponse(rt_cd="1", msg1="조회 실패", data={"stock_code": stock_code})

        output = res.data.get("output") or {}
        cur_str = output.stck_prpr
        open_str = output.stck_oprc

        try:
            cur = float(cur_str) if cur_str not in (None, "N/A") else None
            opn = float(open_str) if open_str not in (None, "N/A") else None
        except (ValueError, TypeError):
            self.logger.warning(
                f"{stock_code} 시가대비 조회 실패: 가격 파싱 오류 (현재가={cur_str}, 시가={open_str})"
            )
            return ResCommonResponse(rt_cd="1", msg1="가격 파싱 오류", data={"stock_code": stock_code})

        vs_val_disp = "N/A"
        vs_rate_disp = "N/A"

        if cur is not None and opn is not None:
            diff = cur - opn
            vs_val_disp = "0" if diff == 0 else f"{diff:+.0f}"
            if opn != 0:
                vs_rate_disp = f"{(diff / opn) * 100:+.2f}%"
            else:
                vs_rate_disp = "N/A"

        data = {
            "stock_code": stock_code,
            "current_price": cur_str,
            "open_price": open_str,
            "vs_open_value_display": vs_val_disp,
            "vs_open_rate_display": vs_rate_disp,
        }
        self.logger.info(
            f"{stock_code} 시가대비 조회 성공: 현재가={cur_str}, 시가={open_str}, "
            f"시가대비={vs_val_disp} ({vs_rate_disp})"
        )
        return ResCommonResponse(rt_cd="0", msg1="정상", data=data)

    async def handle_upper_limit_stocks(self, market_code: str = "0000", limit: int = 500):
        """
        시가총액 상위 종목 조회 (출력 X). TradingService 결과를 표준 스키마로 반환.
        data: List[ResTopMarketCapApiItem]
        """

        try:
            res: ResCommonResponse = await self.trading_service.get_top_market_cap_stocks_code(market_code, limit)
            if not res or res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"시가총액 상위 종목 조회 실패: {res}")
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="시가총액 상위 종목 조회 실패",
                    data=None
                )
            # 성공
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}, 개수={len(res.data) if res.data else 0})")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="조회 성공",
                data=res.data,   # 그대로 전달 (List[ResTopMarketCapApiItem])
            )
        except Exception as e:
            self.logger.exception("시가총액 상위 종목 조회 중 예외")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"예외 발생: {e}",
                data=None
            )

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
            "trading_value": ("거래대금", self.trading_service.get_top_trading_value_stocks, None),
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
                rt_cd=response.rt_cd if response else ErrorCode.API_ERROR.value,
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

    async def handle_program_trading_stream(self, stock_code: str, duration: int = 60) -> None:
        """
        실시간 프로그램매매(H0STPGM0) 구독 → duration초 수신 → 해지.
        UI는 UserActionExecutor에서만 처리하므로 이 레이어는 순수 위임만 수행.
        """
        # 1) 웹소켓 연결 (기본 콜백: TradingService 쪽 핸들러)
        await self.trading_service.connect_websocket()

        # 2) 구독
        await self.trading_service.subscribe_program_trading(stock_code)

        # 3) 지정 시간 대기
        try:
            await self.time_manager.sleep(duration)
        finally:
            # 4) 구독 해지 및 연결 해제 (예외가 나도 정리 보장)
            await self.trading_service.unsubscribe_program_trading(stock_code)
            await self.trading_service.disconnect_websocket()