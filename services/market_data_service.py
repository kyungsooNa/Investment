# services/market_data_service.py
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from config.DynamicConfig import DynamicConfig

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from core.market_clock import MarketClock
from brokers.broker_api_wrapper import BrokerAPIWrapper
from common.types import (
    ResPriceSummary, ResCommonResponse, ErrorCode,
    ResTopMarketCapApiItem, ResBasicStockInfo, ResFluctuation, ResDailyChartApiItem
)
from core.cache.cache_store import CacheStore
from core.performance_profiler import PerformanceProfiler
from services.market_calendar_service import MarketCalendarService

if TYPE_CHECKING:
    from repositories.stock_repository import StockRepository

class MarketDataService:
    """
    시장 데이터(현재가, 호가, OHLCV, 랭킹 등) 조회를 전담하는 서비스입니다.
    """
    def __init__(self, broker_api_wrapper: BrokerAPIWrapper, env: KoreaInvestApiEnv, logger=None,
                 market_clock: MarketClock = None, cache_store: Optional[CacheStore] = None,
                 market_calendar_service: Optional[MarketCalendarService] = None, performance_profiler: Optional[PerformanceProfiler] = None,
                 stock_repository: Optional['StockRepository'] = None):
        self._broker_api_wrapper = broker_api_wrapper
        self._env = env
        self._logger = logger if logger else logging.getLogger(__name__)
        self._market_clock = market_clock
        self.cache_store = cache_store
        self._mcs = market_calendar_service
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._stock_repo = stock_repository
        
        self._ETF_PREFIXES = (
            "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE",
            "HANARO", "KOSEF", "PLUS", "TIMEFOLIO", "WON", "FOCUS",
            "VITA", "TREX", "MASTER", "WOORI", "KINDEX",
        )

    async def get_name_by_code(self, code: str) -> str:
        """종목코드로 종목명을 반환합니다 (BrokerAPIWrapper 위임)."""
        return await self._broker_api_wrapper.get_name_by_code(code)

    async def get_price_summary(self, stock_code) -> ResCommonResponse:
        """주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보를 반환합니다 (KoreaInvestApiQuotations 위임)."""
        self._logger.info(f"MarketDataService - {stock_code} 종목 요약 정보 조회 요청")
        return await self._broker_api_wrapper.get_price_summary(stock_code)

    async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
        """종목코드로 종목의 전체 정보를 가져옵니다 (BrokerAPIWrapper 위임)."""
        self._logger.info(f"MarketDataService - {stock_code} 종목 상세 정보 조회 요청")
        return await self._broker_api_wrapper.get_stock_info_by_code(stock_code)

    async def get_current_price(self, stock_code, count_stats: bool = True, caller: str = "unknown") -> ResCommonResponse:
        # 1. StockRepository 단기 캐시 확인 (웹소켓 틱 갱신 또는 최근 API 조회 결과)
        if self._stock_repo:
            cached_data = self._stock_repo.get_current_price(stock_code, max_age_sec=3.0, count_stats=count_stats, caller=caller)
            if cached_data:
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공(Cache)", data=cached_data)

        # 2. 장 마감 시간대에는 daily_prices DB 스냅샷 확인 (API 호출 절약)
        is_market_open = (await self._mcs.is_market_open_now()) if self._mcs else self._market_clock.is_market_operating_hours()
        if self._stock_repo and not is_market_open:
            db_data = await self._stock_repo.get_latest_daily_snapshot(stock_code)
            if db_data:
                self._stock_repo.set_current_price(stock_code, db_data)
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공(DB)", data=db_data)

        if count_stats:
            self._logger.info(f"MarketDataService - {stock_code} 현재가 조회 요청")
        resp = await self._broker_api_wrapper.get_current_price(stock_code)

        # 3. 조회 결과를 StockRepository에 갱신
        if resp and resp.rt_cd == ErrorCode.SUCCESS.value and self._stock_repo:
            self._stock_repo.set_current_price(stock_code, resp.data)

        return resp

    async def get_stock_conclusion(self, stock_code: str) -> ResCommonResponse:
        """종목의 체결(체결강도 등) 정보를 조회합니다."""
        self._logger.info(f"MarketDataService - {stock_code} 체결 정보 조회 요청")
        return await self._broker_api_wrapper.get_stock_conclusion(stock_code)

    async def get_multi_price(self, stock_codes: list[str]) -> ResCommonResponse:
        """복수종목 현재가 조회 (최대 30종목)"""
        self._logger.info(f"MarketDataService - 복수종목 현재가 조회 요청 ({len(stock_codes)}종목)")
        return await self._broker_api_wrapper.get_multi_price(stock_codes)

    async def get_top_market_cap_stocks_code(self, market_code: str, limit: int = None) -> ResCommonResponse:
        """
        시가총액 상위 종목을 조회하고 결과를 반환합니다 (모의투자 미지원).
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResTopMarketCapApiItem] 포함.
        """
        if limit is None:
            limit = 30
            self._logger.warning(f"[경고] count 파라미터가 명시되지 않아 기본값 30을 사용합니다. market_code={market_code}")

        self._logger.info(f"MarketDataService - 시가총액 상위 종목 조회 요청 - 시장: {market_code}, 개수: {limit}")
        if self._env.is_paper_trading:
            self._logger.warning("MarketDataService - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.")
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1="모의투자 미지원 API입니다.", data=[])

        return await self._broker_api_wrapper.get_top_market_cap_stocks_code(market_code, limit)

    async def get_current_upper_limit_stocks(self, rise_stocks: List) -> ResCommonResponse:
        """
        전체 종목 리스트 중 현재 상한가에 도달한 종목을 필터링합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[Dict] (종목 정보) 포함.
        """
        results: List[ResBasicStockInfo] = []
        for stock_info in rise_stocks:
            code = "Unknown"
            try:
                if isinstance(stock_info, dict):
                    code = stock_info.get("stck_shrn_iscd", "Unknown")
                elif hasattr(stock_info, "stck_shrn_iscd"):
                    code = stock_info.stck_shrn_iscd

                fluctuation_info: ResFluctuation = stock_info
                code = fluctuation_info.stck_shrn_iscd
                current_price = int(fluctuation_info.stck_prpr)
                prdy_ctrt = float(fluctuation_info.prdy_ctrt)
                name = fluctuation_info.hts_kor_isnm
                change_rate = float(fluctuation_info.prdy_vrss)

                if prdy_ctrt > 29.0:
                    stock_info = ResBasicStockInfo(
                        code=code, name=name, current_price=current_price,
                        change_rate=change_rate, prdy_ctrt=prdy_ctrt
                    )
                    results.append(stock_info)
            except Exception as e:
                self._logger.warning(f"{code} 현재 상한가 필터링 중 오류: {e}", exc_info=True)
                continue

        self._market_clock.get_current_kst_time()
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="현재 상한가 종목 필터링 성공", data=results)

    async def get_all_stocks_code(self) -> ResCommonResponse:
        """
        전체 종목 코드를 조회합니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[str] (종목 코드 리스트) 포함.
        """
        self._logger.info("MarketDataService - 전체 종목 코드 조회 요청")
        try:
            codes = await self._broker_api_wrapper.get_all_stock_code_list()
            if not isinstance(codes, list):
                msg = f"비정상 응답 형식: {codes}"
                self._logger.warning(msg)
                return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=msg, data=[])
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="전체 종목 코드 조회 성공", data=codes)
        except Exception as e:
            error_msg = f"전체 종목 코드 조회 실패: {e}"
            self._logger.exception(error_msg)
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=error_msg, data=[])

    async def get_asking_price(self, stock_code: str) -> ResCommonResponse:
        """종목의 실시간 호가 정보를 조회합니다."""
        self._logger.info(f"MarketDataService - {stock_code} 종목 호가 정보 조회 요청")
        return await self._broker_api_wrapper.get_asking_price(stock_code)

    async def get_time_concluded_prices(self, stock_code: str) -> ResCommonResponse:
        """종목의 시간대별 체결가 정보를 조회합니다."""
        self._logger.info(f"MarketDataService - {stock_code} 종목 시간대별 체결가 조회 요청")
        return await self._broker_api_wrapper.get_time_concluded_prices(stock_code)

    async def inquire_daily_itemchartprice(self, stock_code: str, start_date: str, end_date: str, fid_period_div_code: str = 'D') -> ResCommonResponse:
        """일별/분봉 주식 시세 차트 데이터를 조회합니다 (KoreaInvestApiQuotations 위임)."""
        self._logger.info(f"MarketDataService - {stock_code} 종목 차트 데이터 조회 요청")
        return await self._broker_api_wrapper.inquire_daily_itemchartprice(stock_code=stock_code, start_date=start_date, end_date=end_date, fid_period_div_code=fid_period_div_code)

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """상승률 또는 하락률 상위 종목을 조회합니다."""
        direction = "상승" if rise else "하락"
        self._logger.info(f"MarketDataService - {direction}률 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """거래량 상위 종목을 조회합니다."""
        self._logger.info("MarketDataService - 거래량 상위 종목 조회 요청")
        return await self._broker_api_wrapper.get_top_volume_stocks()

    async def get_top_trading_value_stocks(self) -> ResCommonResponse:
        """
        거래대금 상위 종목 조회.
        거래량/코스피시가총액30/코스닥시가총액30/상승률/하락률 5개 기존 API 결과를 병합하여
        acml_tr_pbmn(거래대금) 기준 상위 30개를 반환한다.
        ETF/ETN 종목은 제외한다.
        """
        t_start = self.pm.start_timer()
        self._logger.info("MarketDataService - 거래대금 상위 종목 조회 요청 (병합)")

        vol_resp, mc_kospi_resp, mc_kosdaq_resp, rise_resp, fall_resp = await asyncio.gather(
            self._broker_api_wrapper.get_top_volume_stocks(),
            self._broker_api_wrapper.get_top_market_cap_stocks_code("0000", 30),
            self._broker_api_wrapper.get_top_market_cap_stocks_code("1001", 30),
            self._broker_api_wrapper.get_top_rise_fall_stocks(True),
            self._broker_api_wrapper.get_top_rise_fall_stocks(False),
        )

        volume_stocks = vol_resp.data if vol_resp and vol_resp.rt_cd == ErrorCode.SUCCESS.value else []
        if isinstance(volume_stocks, dict):
            volume_stocks = volume_stocks.get("output", [])
        mc_kospi = mc_kospi_resp.data if mc_kospi_resp and mc_kospi_resp.rt_cd == ErrorCode.SUCCESS.value else []
        mc_kosdaq = mc_kosdaq_resp.data if mc_kosdaq_resp and mc_kosdaq_resp.rt_cd == ErrorCode.SUCCESS.value else []
        mc_stocks = list(mc_kospi or []) + list(mc_kosdaq or [])
        rise_stocks = rise_resp.data if rise_resp and rise_resp.rt_cd == ErrorCode.SUCCESS.value else []
        fall_stocks = fall_resp.data if fall_resp and fall_resp.rt_cd == ErrorCode.SUCCESS.value else []

        def _to_dict(item):
            return item.to_dict() if hasattr(item, 'to_dict') else (item if isinstance(item, dict) else {})
        def _get_code(d):
            return d.get("mksc_shrn_iscd") or d.get("stck_shrn_iscd") or d.get("iscd") or ""

        merged = {}
        for stock in volume_stocks:
            d = _to_dict(stock)
            code = _get_code(d)
            if code: merged[code] = d

        for stock in mc_stocks + list(rise_stocks or []) + list(fall_stocks or []):
            d = _to_dict(stock)
            code = _get_code(d)
            if code and code not in merged:
                if not d.get("acml_tr_pbmn"):
                    try:
                        d["acml_tr_pbmn"] = str(int(d.get("stck_prpr", "0") or "0") * int(d.get("acml_vol", "0") or "0"))
                    except (ValueError, TypeError):
                        d["acml_tr_pbmn"] = "0"
                merged[code] = d

        merged = {c: d for c, d in merged.items() if not self._is_etf(d)}
        result = list(merged.values())
        result.sort(key=lambda x: int(x.get("acml_tr_pbmn", "0") or "0"), reverse=True)
        result = result[:30]
        for i, stock in enumerate(result, 1):
            stock["data_rank"] = str(i)

        self.pm.log_timer("MarketDataService.get_top_trading_value_stocks", t_start, threshold=1.0)
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="거래대금 상위 성공", data=result)

    def _is_etf(self, stock: dict) -> bool:
        """종목명 기반 ETF/ETN 여부 판별."""
        name = stock.get("hts_kor_isnm") or stock.get("kor_shrt_ism") or ""
        return any(name.startswith(prefix) for prefix in self._ETF_PREFIXES)

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """특정 ETF의 상세 정보를 조회합니다."""
        self._logger.info(f"MarketDataService - {etf_code} ETF 정보 조회")
        return await self._broker_api_wrapper.get_etf_info(etf_code)

    async def get_financial_ratio(self, stock_code: str) -> ResCommonResponse:
        """특정 종목의 재무비율을 조회합니다."""
        self._logger.info(f"MarketDataService - {stock_code} 재무비율 조회")
        return await self._broker_api_wrapper.get_financial_ratio(stock_code)

    def _normalize_ohlcv_rows(self, items: List[Any]) -> List[dict]:
        """
        한국투자 일봉 응답(ResDailyChartApiItem list) 또는 dict list를
        표준 OHLCV 스키마로 정규화한다.
          반환: [{"date":"YYYYMMDD","open":float,"high":float,"low":float,"close":float,"volume":int}, ...]
        """
        def _get(it, attr, default=None):
            if it is None: return default
            if isinstance(it, dict): return it.get(attr, default)
            return getattr(it, attr, default)
        def _to_float(x):
            try: return float(str(x).replace(",", ""))
            except Exception: return None
        def _to_int(x):
            try: return int(float(str(x).replace(",", "")))
            except Exception: return None
        rows = []
        for it in items or []:
            date = _get(it, "stck_bsop_date") or _get(it, "date")
            if not date: continue
            rows.append({
                "date": date,
                "open": _to_float(_get(it, "stck_oprc") or _get(it, "open")),
                "high": _to_float(_get(it, "stck_hgpr") or _get(it, "high")),
                "low": _to_float(_get(it, "stck_lwpr") or _get(it, "low")),
                "close": _to_float(_get(it, "stck_clpr") or _get(it, "close")),
                "volume": _to_int(_get(it, "acml_vol") or _get(it, "volume")),
            })
        rows.sort(key=lambda r: r["date"])
        return rows

    def _calc_range_by_period(self, period: str, end_dt: datetime | None, limit: int | None = None) -> tuple[str, str]:
        """
        period: 'D'|'W'|'M'
        end_dt: 기준일(없으면 now KST)
        limit : 원하는 봉 개수(없으면 합리적 기본값)
        return: (start_yyyymmdd, end_yyyymmdd)
        """
        if end_dt is None: end_dt = self._market_clock.get_current_kst_time()
        period = (period or "D").upper()
        if period == "D": start_dt = end_dt - timedelta(days=max((limit or 365) * 2, 730))
        elif period == "W": start_dt = end_dt - timedelta(weeks=max((limit or 52) * 2, 104))
        elif period == "M": start_dt = end_dt - timedelta(days=max((limit or 24) * 2, 60) * 31)
        else: start_dt = end_dt - timedelta(days=max((limit or 120) * 2, 240))
        return self._market_clock.to_yyyymmdd(start_dt), self._market_clock.to_yyyymmdd(end_dt)

    async def _fetch_past_daily_ohlcv(self, stock_code: str, end_yyyymmdd: str, max_loops: int = 8) -> List[dict]:
        """
        과거 일봉 데이터를 반복 조회하여 수집합니다.
        :param end_yyyymmdd: 조회 종료일 (보통 어제 날짜)
        :param max_loops: 반복 횟수 (10회 * 100일 = 약 1000일 ≈ 692 거래일)
        """
        t_start = self.pm.start_timer()
        tasks = []
        curr_end_dt = datetime.strptime(end_yyyymmdd, "%Y%m%d")
        
        for _ in range(max_loops):
            curr_start_dt = curr_end_dt - timedelta(days=DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE)
            s_date = self._market_clock.to_yyyymmdd(curr_start_dt)
            e_date = self._market_clock.to_yyyymmdd(curr_end_dt)
            
            tasks.append(self._broker_api_wrapper.inquire_daily_itemchartprice(
                stock_code=stock_code, start_date=s_date, end_date=e_date, fid_period_div_code="D"
            ))
            curr_end_dt = curr_start_dt - timedelta(days=1)

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        all_rows_map = {}
        for raw in responses:
            if isinstance(raw, Exception) or not raw or raw.rt_cd != ErrorCode.SUCCESS.value:
                continue
            rows = self._normalize_ohlcv_rows(raw.data)
            for r in rows:
                all_rows_map[r['date']] = r
                
        all_rows = sorted(all_rows_map.values(), key=lambda x: x['date'])
        
        self.pm.log_timer(f"MarketData._fetch_past_daily_ohlcv({stock_code})", t_start)
        return all_rows

    async def _fetch_today_ohlcv(self, stock_code: str, today_str: str, caller: str = "unknown") -> List[dict]:
        try:
            current_resp = await self.get_current_price(stock_code, caller=caller)
            if current_resp.rt_cd == ErrorCode.SUCCESS.value and current_resp.data:
                output = current_resp.data.get('output')
                if output:
                    def _get_val(obj, attr_name):
                        return obj.get(attr_name) if isinstance(obj, dict) else getattr(obj, attr_name, None)
                    opn = _get_val(output, 'stck_oprc')
                    high = _get_val(output, 'stck_hgpr')
                    low = _get_val(output, 'stck_lwpr')
                    close = _get_val(output, 'stck_prpr')
                    vol = _get_val(output, 'acml_vol')
                    if opn and high and low and close:
                        return [{"date": today_str, "open": float(opn), "high": float(high), "low": float(low), "close": float(close), "volume": int(vol) if vol else 0}]
        except Exception as e:
            self._logger.warning(f"오늘자 OHLCV 구성을 위한 현재가 조회 실패: {e}")
        return []

    async def get_ohlcv(self, stock_code: str, period: str = "D", caller: str = "unknown") -> ResCommonResponse:
        """
        일봉(D)의 경우 StockRepository 활용, 주봉/월봉은 기존 API 조회 방식 사용.
        """
        t_ohlcv = self.pm.start_timer()
        if (period or "D").upper() == "D":
            now_dt = self._market_clock.get_current_kst_time()
            today_str = now_dt.strftime("%Y%m%d")
            yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y%m%d")
            past_rows = []

            # 1. 과거 데이터 가져오기 (StockRepository 캐시/DB 활용)
            if self._stock_repo:
                stock_data = await self._stock_repo.get_stock_data(stock_code, ohlcv_limit=600, caller=caller)
                if stock_data and "ohlcv" in stock_data:
                    past_rows = stock_data["ohlcv"]

            # 2. 로컬 DB에 데이터가 부족하거나 없으면 전체 API 백필 (약 1000일 ≈ 692 거래일)
            if not past_rows or len(past_rows) < 600:
                past_rows = await self._fetch_past_daily_ohlcv(stock_code, yesterday_str, max_loops=10)
                if self._stock_repo and past_rows:
                    await self._stock_repo.upsert_ohlcv([{**r, "code": stock_code} for r in past_rows])

            # 3. 오늘 데이터 처리 (실시간 API 병합) - 장 마감 후에는 불필요
            today_rows = []
            is_market_open = (await self._mcs.is_market_open_now()) if self._mcs else False

            if not is_market_open:
                pass
            else:
                today_rows = await self._fetch_today_ohlcv(stock_code, today_str, caller=caller)
                if today_rows and now_dt.weekday() >= 5: today_rows = []
                elif today_rows and past_rows:
                    last_past = past_rows[-1]
                    today = today_rows[0]
                    if today['date'] != last_past['date'] and (today['open'] == last_past['open'] and today['close'] == last_past['close']):
                        today_rows = []

            if today_rows and not is_market_open:
                if not past_rows or today_rows[0]['date'] > past_rows[-1]['date']:
                    if self._stock_repo: await self._stock_repo.upsert_ohlcv([{**today_rows[0], "code": stock_code}])
                    past_rows = past_rows + today_rows
                    today_rows = []

            merged_map = {r['date']: r for r in past_rows}
            for r in today_rows: merged_map[r['date']] = r
            final_rows = sorted(merged_map.values(), key=lambda x: x['date'])
            self.pm.log_timer(f"MarketData.get_ohlcv({stock_code})", t_ohlcv)
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=f"OHLCV {len(final_rows)}건", data=final_rows)
        else:
            s, e = self._calc_range_by_period(period, end_dt=self._market_clock.get_current_kst_time())
            raw = await self._broker_api_wrapper.inquire_daily_itemchartprice(stock_code, start_date=s, end_date=e, fid_period_div_code=period.upper())
            if not raw or raw.rt_cd != ErrorCode.SUCCESS.value: return raw
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=self._normalize_ohlcv_rows(raw.data))

    async def get_ohlcv_range(self, stock_code: str, period: str = "D", start_date: Optional[str] = None, end_date: Optional[str] = None) -> ResCommonResponse:
        """
        시작일~종료일 범위형 차트 API 호출 (일/분 공통).
        """
        ed = end_date or self._market_clock.get_current_kst_time()
        sd = start_date or (datetime.strptime(ed, "%Y%m%d") - timedelta(days=240)).strftime("%Y%m%d")
        raw = await self._broker_api_wrapper.inquire_daily_itemchartprice(stock_code, start_date=sd, end_date=ed, fid_period_div_code=period.upper())
        if not raw or raw.rt_cd != ErrorCode.SUCCESS.value: return raw
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=self._normalize_ohlcv_rows(raw.data))

    async def get_recent_daily_ohlcv(self, code: str, limit: int = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE, end_date: Optional[str] = None, start_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        최근 'limit'개 *거래일* 일봉을 반환.
        API는 시작/종료일 범위를 요구하므로 넉넉한 범위로 받아서 슬라이스로 limit개 보장.
        한국투자증권 API 특성상 긴 기간(1년 이상) 조회 시 데이터가 잘릴 수 있으므로,
        1년 단위로 끊어서 반복 호출하여 병합한다.
        """
        t_start = self.pm.start_timer()
        current_ed_dt = datetime.strptime(end_date, "%Y%m%d") if end_date else self._market_clock.get_current_kst_time()
        
        if start_date:
            resp = await self.get_ohlcv_range(code, "D", start_date, self._market_clock.to_yyyymmdd(current_ed_dt))
            return resp.data or [] if resp and resp.rt_cd == ErrorCode.SUCCESS.value else []

        # limit을 만족하기 위해 대략 영업일을 고려해 1.5를 곱하고 100일 단위로 끊습니다.
        max_loops = int((limit * 1.5) // 100) + 1
        max_loops = max(1, min(max_loops, 20))  # 최소 1, 최대 20으로 한정

        tasks = []
        curr_end_dt = current_ed_dt
        for _ in range(max_loops):
            ed_str = self._market_clock.to_yyyymmdd(curr_end_dt)
            curr_start_dt = curr_end_dt - timedelta(days=100)
            sd_str = self._market_clock.to_yyyymmdd(curr_start_dt)
            
            tasks.append(self.get_ohlcv_range(code, "D", sd_str, ed_str))
            curr_end_dt = curr_start_dt - timedelta(days=1)

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        all_rows_map = {}
        for resp in responses:
            if isinstance(resp, Exception) or not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                continue
            for r in (resp.data or []):
                all_rows_map[r['date']] = r

        all_rows = sorted(all_rows_map.values(), key=lambda x: x['date'])
        
        if len(all_rows) > limit: 
            all_rows = all_rows[-limit:]
            
        self.pm.log_timer(f"MarketData.get_recent_daily_ohlcv({code})", t_start)
        return all_rows

    async def get_intraday_minutes_today(self, *, stock_code: str, input_hour_1: str) -> ResCommonResponse:
        """
        URL: /quotations/inquire-time-itemchartprice
        TR : FHKST03010200 (모의/실전 공통)
        """
        return await self._broker_api_wrapper.inquire_time_itemchartprice(stock_code=stock_code, input_hour_1=input_hour_1, pw_data_incu_yn="Y", etc_cls_code="0")

    async def get_intraday_minutes_by_date(self, *, stock_code: str, input_date_1: str, input_hour_1: str = "") -> ResCommonResponse:
        """
        URL: /quotations/inquire-time-dailychartprice
        TR : FHKST03010230 (실전만)
        """
        if self._env.is_paper_trading: return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="모의투자 미지원", data=[])
        return await self._broker_api_wrapper.inquire_time_dailychartprice(stock_code=stock_code, input_date_1=input_date_1, input_hour_1=input_hour_1, pw_data_incu_yn="Y", fake_tick_incu_yn="")

    async def get_latest_trading_date(self) -> Optional[str]:
        """
        대표 종목(삼성전자)의 일봉을 조회하여 데이터가 존재하는 가장 최근 날짜를 반환한다.
        """
        if self._mcs: return await self._mcs.get_latest_trading_date()
        return None

    async def get_next_open_day(self) -> Optional[str]:
        """
        현재 날짜 기준으로 가장 빠른 개장일을 찾아 반환합니다 (YYYYMMDD).
        국내휴장일조회 API를 사용하여 휴장 여부를 정확히 판단합니다.
        """
        current_date = self._market_clock.get_current_kst_time().date()
        target_date_str = current_date.strftime("%Y%m%d")
        for _ in range(30):
            resp = await self._broker_api_wrapper.check_holiday(target_date_str)
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                current_date += timedelta(days=1)
                target_date_str = current_date.strftime("%Y%m%d")
                continue
            outputs = resp.data.get("output", []) if isinstance(resp.data, dict) else []
            for item in outputs:
                if item.get("bzdy_yn") == "Y": return item.get("bass_dt")
            if outputs:
                last_dt_str = outputs[-1].get("bass_dt", target_date_str)
                current_date = datetime.strptime(last_dt_str, "%Y%m%d").date() + timedelta(days=1)
                target_date_str = current_date.strftime("%Y%m%d")
        return None