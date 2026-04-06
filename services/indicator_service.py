import logging
import pandas as pd
import numpy as np
import math
import time
from datetime import datetime
from typing import List, Dict, Optional, TYPE_CHECKING, Union
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage, ResRelativeStrength
from core.cache.cache_store import CacheStore
from core.performance_profiler import PerformanceProfiler

if TYPE_CHECKING:
    from services.stock_query_service import StockQueryService

class IndicatorService:
    """
    기술적 지표 계산을 담당하는 서비스.
    StockQueryService를 통해 데이터를 조회하고 가공하여 지표 값을 반환합니다.
    """
    def __init__(self, stock_query_service: Optional['StockQueryService'] = None, 
                 cache_store: Optional[CacheStore] = None, 
                 performance_profiler: Optional[PerformanceProfiler] = None):
        self.stock_query_service = stock_query_service
        self.cache_store = cache_store
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)

    @staticmethod
    def _safe_float(val):
        if val is None or pd.isna(val):
            return None
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    async def _get_ohlcv_data(self, stock_code: str, candle_type: str, ohlcv_data: Optional[List[Dict]] = None) -> tuple:
        """
        OHLCV 데이터를 가져옵니다. ohlcv_data가 전달되면 API 호출을 생략합니다.
        Returns: (ohlcv_data, error_response) - 성공 시 error_response는 None
        """
        if ohlcv_data is not None:
            return ohlcv_data, None

        if not self.stock_query_service:
            return None, ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="StockQueryService not initialized", data=None)

        resp = await self.stock_query_service.get_ohlcv(stock_code, period=candle_type)
        if resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return None, resp
        return resp.data, None

    async def _get_with_incremental_cache(
        self, 
        stock_code: str, 
        candle_type: str, 
        indicator_name: str, 
        data: List[Dict], 
        lookback_period: int, 
        calc_func: callable, 
        *calc_args
    ) -> ResCommonResponse:
        """
        [공통 지표 캐싱 & 병합 파이프라인]
        과거 확정 데이터는 캐싱하고, 당일(미확정) 데이터만 증분 계산하여 O(1)로 병합합니다.
        
        :param stock_code: 종목코드
        :param candle_type: 캔들 타입 ("D", "W", "M" 등)
        :param indicator_name: 캐시 키 생성을 위한 지표명 (예: "rsi_14", "bb_20_2.0")
        :param data: 전체 OHLCV 데이터
        :param lookback_period: 증분 계산 시 필요한 최소 과거 데이터 개수 (예: RSI 14면 14)
        :param calc_func: 실제 지표 계산을 수행하고 List[Dict]를 반환하는 콜백 함수
        :param calc_args: calc_func에 전달할 추가 인자들
        """
        # 1. 예외 처리 및 캐시 미적용 조건 (데이터가 너무 적거나, 일봉이 아니거나, 캐시가 꺼진 경우)
        if not self.cache_store or candle_type != "D" or len(data) <= lookback_period:
            resp = calc_func(stock_code, data, *calc_args)
            # [수정포인트 1] 이미 ResCommonResponse 객체라면 이중 래핑 방지
            return resp if isinstance(resp, ResCommonResponse) else ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공(NoCache)", data=resp)
        
        # 2. 확정 데이터(어제까지)와 당일 데이터 분리
        confirmed_data = data[:-1]
        confirmed_last_date = str(confirmed_data[-1]['date'])
        
        # 캐시 키 생성 (지표명_종목코드_마지막확정일자)
        cache_key = f"{indicator_name}_{stock_code}_{confirmed_last_date}"

        # 3. 캐시 조회
        cached_result = self.cache_store.get(cache_key)

        # 4. 캐시 미스: 확정 데이터 전체에 대해 계산 후 캐시 저장
        if not cached_result:
            calc_resp = calc_func(stock_code, confirmed_data, *calc_args)
            # [수정포인트 2] 반환값이 ResCommonResponse면 내부 data만 추출해서 캐싱
            if isinstance(calc_resp, ResCommonResponse):
                if calc_resp.rt_cd != ErrorCode.SUCCESS.value:
                    return calc_resp # 에러 발생 시 즉시 반환
                cached_result = calc_resp.data
            else:
                cached_result = calc_resp
                
            self.cache_store.set(cache_key, cached_result)

        # 5. 당일 증분 계산
        slice_size = lookback_period + 5
        partial_data = data[-slice_size:]
        
        partial_resp = calc_func(stock_code, partial_data, *calc_args)
        
        # [수정포인트 3] 반환값이 ResCommonResponse면 내부 data만 추출해서 병합 준비
        if isinstance(partial_resp, ResCommonResponse):
            if partial_resp.rt_cd != ErrorCode.SUCCESS.value:
                return partial_resp
            partial_result = partial_resp.data
        else:
            partial_result = partial_resp
            
        if not partial_result:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1="부분 지표 계산 실패", data=None)
            
        latest_indicator = partial_result[-1]
        # 6. O(1) 속도로 병합 (리스트 '+' 연산자 제거 최적화)
        final_data = cached_result.copy() # 얕은 복사로 원본 캐시 리스트 보호
        
        if final_data and final_data[-1]['date'] == latest_indicator['date']:
            final_data[-1] = latest_indicator # 덮어쓰기
        else:
            final_data.append(latest_indicator) # 맨 뒤에 추가

        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공(CacheHit)", data=final_data)

    async def get_bollinger_bands(self, stock_code: str, period: int = 20, multiplier: float = 2.0, candle_type: str = "D",
                                 ohlcv_data: Optional[List[Dict]] = None) -> ResCommonResponse:
        """볼린저 밴드 조회"""
        data, err_resp = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data=ohlcv_data)
        if err_resp: return err_resp

        return await self._get_with_incremental_cache(
            stock_code,
            candle_type,
            f"bb_{period}_{multiplier}",
            data,
            period,
            self._calculate_bollinger_bands_full, # DataFrame 변환 및 순수 계산 로직만 있는 내부 함수
            period,      # *calc_args 첫 번째
            multiplier   # *calc_args 두 번째
        )

    async def get_rsi(self, stock_code: str, period: int = 14, candle_type: str = "D",
                                 ohlcv_data: Optional[List[Dict]] = None) -> ResCommonResponse:
        """RSI (상대강도지수) 조회"""
        # 1. OHLCV 데이터 가져오기
        data, err_resp = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data=ohlcv_data)
        if err_resp: return err_resp

        # 2. 공통 파이프라인에 위임 (순수 계산 함수인 _calculate_rsi_series_internal 등을 넘김)
        return await self._get_with_incremental_cache(
            stock_code,
            candle_type,
            f"rsi_{period}",
            data,
            period,
            self._calculate_rsi_series, # DataFrame 변환 및 순수 계산 로직만 있는 내부 함수
            period  # <-- calc_func에 전달될 *calc_args (정상 작동!)
        )

    async def get_moving_average(
        self, 
        stock_code: str, 
        period: int = 20, 
        method: str = "sma", 
        candle_type: str = "D", 
        ohlcv_data: Optional[List[Dict]] = None
    ) -> ResCommonResponse:
        """이동평균선 조회"""
        # 1. OHLCV 데이터 로드 및 에러 처리
        data, err_resp = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data=ohlcv_data)

        if err_resp: return err_resp


        # 2. 공통 캐시 파이프라인에 위임 (method 파라미터 포함)
        return await self._get_with_incremental_cache(
            stock_code,
            candle_type,
            f"ma_{period}_{method}",              # [수정됨] 캐시 키에 method 포함 (예: ma_20_sma)
            data,
            period,
            self._calculate_moving_average_full,  # 기존 이동평균선 전체 계산 함수
            period,                               # *calc_args 첫 번째 인자
            method                                # [수정됨] *calc_args 두 번째 인자로 method 전달
        )

    async def get_relative_strength(
        self, 
        stock_code: str, 
        period_days: int = 63,
        candle_type: str = "D",
        ohlcv_data: Optional[List[Dict]] = None
    ) -> ResCommonResponse:
        """N일 수익률(상대강도 원시값)을 계산하여 반환합니다.
        (단일 스칼라 값만 필요하므로, Pandas 변환 및 캐싱 없이 O(1) 리스트 직접 연산으로 초고속 처리합니다.)
        """
        t_start = self.pm.start_timer()
        t_calc = self.pm.start_timer()
        try:
            # 1. OHLCV 데이터 로드
            data, err_resp = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
            if err_resp: return err_resp

            # pct_change(period_days)와 동일한 간격을 구하려면 데이터가 period_days + 1 개 필요합니다.
            if len(data) < period_days + 1:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1=f"데이터 부족: {len(data)} < {period_days + 1}",
                    data=None
                )

            # 2. 무거운 DataFrame 변환 없이 리스트 인덱싱으로 즉시 추출!
            recent_candle = data[-1]                  # 오늘(최근) 캔들
            past_candle = data[-(period_days + 1)]    # N일 전 캔들 (예: 63일 전)

            recent_close = self._safe_float(recent_candle.get('close'))
            past_close = self._safe_float(past_candle.get('close'))

            # 데이터 유효성 검증
            if recent_close is None or past_close is None or past_close <= 0:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1=f"유효하지 않은 종가 (past_close={past_close})",
                    data=None
                )

            # 3. 수익률 계산
            return_pct = ((recent_close - past_close) / past_close) * 100

            result = ResRelativeStrength(
                code=stock_code,
                date=str(recent_candle.get('date')),
                return_pct=round(return_pct, 2),
            )
            
            if self.pm.enabled:
                self.pm.log_timer(f"IndicatorService.get_relative_strength({stock_code})", t_start)

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=result)

        except Exception as e:
            logging.getLogger(__name__).exception(f"상대강도 계산 중 오류: {e}")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"상대강도 계산 중 오류: {str(e)}",
                data=None
            )

    async def get_chart_indicators(self, stock_code: str, ohlcv_data: List[Dict]) -> ResCommonResponse:
        """
        차트 렌더링용 지표(MA 5/10/20/60/120, BB, RS)를 한 번에 계산하여 반환합니다.
        과거 데이터에 대한 계산 결과를 캐싱하여 성능을 최적화합니다.
        """
        t_start = self.pm.start_timer()
        
        # 데이터가 너무 적거나 캐시 매니저가 없으면 전체 계산 (최대 기간 120일 + 여유)
        if not ohlcv_data or len(ohlcv_data) < 130 or not self.cache_store:
             resp = self._calculate_indicators_full(stock_code, ohlcv_data)
             self.pm.log_timer(f"IndicatorService.get_chart_indicators({stock_code})", t_start, extra_info="Full Calc", threshold=0.5)
             return resp

        try:
            # 1. 확정된 과거 데이터 분리 (마지막 데이터 제외)
            # 마지막 데이터는 장 중 실시간으로 변할 수 있으므로 캐시 대상에서 제외
            confirmed_data = ohlcv_data[:-1]
            confirmed_len = len(confirmed_data)
            # 캐시 키: 시작일 + 종료일 + 데이터 수로 구성하여 ohlcv_limit 변경이나
            # DB 행 수 변화로 인한 캐시 불일치 방지
            confirmed_first_date = str(confirmed_data[0]['date'])
            confirmed_last_date = str(confirmed_data[-1]['date'])

            cache_key = f"indicators_chart_{stock_code}_{confirmed_first_date}_{confirmed_last_date}_{confirmed_len}"

            # 2. 캐시 조회
            raw_cache = self.cache_store.get_raw(cache_key)
            cached_wrapper = None
            if raw_cache and isinstance(raw_cache, tuple):
                cached_wrapper, _ = raw_cache

            past_indicators = None
            if cached_wrapper:
                cached_data = cached_wrapper.get('data')
                # 캐시된 지표 행 수가 confirmed_data와 일치하는지 검증
                if cached_data:
                    sample_key = next((k for k, v in cached_data.items() if isinstance(v, list)), None)
                    if sample_key and len(cached_data[sample_key]) == confirmed_len:
                        past_indicators = cached_data

            # 3. 캐시 미스 시 과거 데이터 전체 계산 및 저장
            if not past_indicators:
                resp = self._calculate_indicators_full(stock_code, confirmed_data)
                if resp.rt_cd != ErrorCode.SUCCESS.value:
                    return resp
                past_indicators = resp.data

                # 캐시 저장 (파일 저장 포함)
                self.cache_store.set(cache_key, {
                    "timestamp": datetime.now().isoformat(),
                    "data": past_indicators
                }, save_to_file=True)

            # 4. 오늘 데이터(마지막 1개)에 대한 지표 계산 (증분 계산)
            # 이동평균 등 계산을 위해 과거 데이터 일부가 필요함 (최대 120일 + 여유)
            lookback = 130
            partial_data = ohlcv_data[-lookback:]
            
            resp_partial = self._calculate_indicators_full(stock_code, partial_data)
            if resp_partial.rt_cd != ErrorCode.SUCCESS.value:
                return resp_partial
            
            latest_indicators = resp_partial.data
            
            # 5. 병합 (과거 지표 + 오늘 지표)
            merged_indicators = {}
            
            for key, val_list in past_indicators.items():
                if isinstance(val_list, list):
                    # latest_indicators[key]의 마지막 요소(오늘치) 가져오기
                    if key in latest_indicators and latest_indicators[key]:
                        latest_item = latest_indicators[key][-1]
                        merged_list = val_list.copy()
                        if merged_list and merged_list[-1].get('date') == latest_item.get('date'):
                            merged_list[-1] = latest_item
                        else:
                            merged_list.append(latest_item)
                        merged_indicators[key] = merged_list
                    else:
                        merged_indicators[key] = val_list
                else:
                    merged_indicators[key] = val_list

            self.pm.log_timer(f"IndicatorService.get_chart_indicators({stock_code})", t_start, extra_info="Cached")
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=merged_indicators)

        except Exception as e:
            logging.getLogger(__name__).exception(f"지표 캐싱 처리 중 오류: {e}")
            # 오류 발생 시 안전하게 전체 재계산 시도
            return self._calculate_indicators_full(stock_code, ohlcv_data)

    # ── 계산 로직 공통화 (Helper Methods) ─────────────────────────────

    def _to_dataframe(self, ohlcv_data: list) -> pd.DataFrame:
        """리스트 데이터를 데이터프레임으로 변환하고 수치 전처리를 수행"""
        try:
            df = pd.DataFrame(ohlcv_data)
            
            if df.empty:
                return df

            # 1. 수치형 변환 (errors='coerce'는 숫자가 아닌 것을 NaN으로 만듭니다)
            if 'close' in df.columns:
                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                
                # 2. [핵심] 무한대(inf) 값을 실제 NaN으로 치환
                # np.inf를 쓰려면 위에 import numpy as np가 필요합니다.
                df['close'] = df['close'].replace([np.inf, -np.inf], np.nan)
            
            return df
            
        except Exception as e:
            # 여기서 에러가 나면 IndicatorService에서 999 에러를 반환하게 됩니다.
            raise e

    @staticmethod
    def _compute_ma(df: pd.DataFrame, period: int, method: str = "sma", target_col: str = "ma") -> pd.DataFrame:
        """이동평균 계산 및 컬럼 추가"""
        if method.lower() == "ema":
            df[target_col] = df['close'].ewm(span=period, adjust=False).mean()
        else:
            df[target_col] = df['close'].rolling(window=period).mean()
        return df

    @staticmethod
    def _compute_bb(df: pd.DataFrame, period: int, std_dev: float, prefix: str = "bb") -> pd.DataFrame:
        """볼린저 밴드 계산 및 컬럼 추가"""
        mb = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std(ddof=0)
        df[f'{prefix}_middle'] = mb
        df[f'{prefix}_upper'] = mb + (std * std_dev)
        df[f'{prefix}_lower'] = mb - (std * std_dev)
        return df

    @staticmethod
    def _compute_rsi(df: pd.DataFrame, period: int, target_col: str = "rsi") -> pd.DataFrame:
        """RSI 계산 및 컬럼 추가"""
        delta = df['close'].diff()
        u = delta.clip(lower=0)
        d = -1 * delta.clip(upper=0)
        au = u.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        ad = d.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        rs = au / ad
        df[target_col] = 100 - (100 / (1 + rs))
        return df

    def calc_bb_widths_sync(
        self,
        ohlcv_data: List[Dict],
        period: int = 20,
        multiplier: float = 2.0,
    ) -> List[float]:
        """이미 확보한 OHLCV 데이터로 BB 폭(upper-lower) 목록을 동기 계산합니다.
        async 오버헤드 없이 순수 pandas 계산만 수행합니다."""
        resp = self._calculate_bollinger_bands_full("", ohlcv_data, period, multiplier)
        if resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return []
        widths = []
        for band in resp.data:
            upper = band.get('upper') if isinstance(band, dict) else getattr(band, 'upper', None)
            lower = band.get('lower') if isinstance(band, dict) else getattr(band, 'lower', None)
            if upper is not None and lower is not None:
                widths.append(upper - lower)
        return widths

    def calc_rs_sync(
        self,
        ohlcv_data: List[Dict],
        period_days: int = 63,
    ) -> float:
        """이미 확보한 OHLCV 데이터로 RS 수익률을 동기 계산합니다.
        async 오버헤드 없이 O(1) 리스트 인덱싱만 수행합니다."""
        try:
            if len(ohlcv_data) < period_days + 1:
                return 0.0
            recent_close = self._safe_float(ohlcv_data[-1].get('close'))
            past_close = self._safe_float(ohlcv_data[-(period_days + 1)].get('close'))
            if recent_close is None or past_close is None or past_close <= 0:
                return 0.0
            return round(((recent_close - past_close) / past_close) * 100, 2)
        except Exception:
            return 0.0

    def _calculate_bollinger_bands_full(self, stock_code, data, period, std_dev) -> ResCommonResponse:
        """볼린저 밴드 전체 계산 (내부용)"""
        try:
            df = self._to_dataframe(data)
            
            df['MB'] = df['close'].rolling(window=period).mean()
            df['std'] = df['close'].rolling(window=period).std(ddof=0)
            df['UB'] = df['MB'] + (df['std'] * std_dev)
            df['LB'] = df['MB'] - (df['std'] * std_dev)
            
            results = [
                {
                    "code": stock_code, "date": str(row.date), 
                    "close": self._safe_float(row.close),
                    "middle": self._safe_float(row.MB), 
                    "upper": self._safe_float(row.UB), 
                    "lower": self._safe_float(row.LB)
                }
                for row in df.itertuples(index=False)
            ]
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=results)
        except Exception as e:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)

    def _calculate_rsi_series(self, stock_code, data, period) -> ResCommonResponse:
        """RSI 시계열 전체 계산 (내부용)"""
        try:
            df = self._to_dataframe(data)
            
            # 공통 로직 사용
            df = self._compute_rsi(df, period, target_col="rsi")
            
            results = [
                {
                    "code": stock_code, "date": str(row.date), 
                    "close": self._safe_float(row.close), 
                    "rsi": self._safe_float(row.rsi)
                }
                for row in df.itertuples(index=False)
            ]
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=results)
        except Exception as e:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)

    def _calculate_moving_average_full(self, stock_code, data, period, method) -> ResCommonResponse:
        """이동평균 전체 계산 (내부용)"""
        try:
            df = self._to_dataframe(data)
            
            # 공통 로직 사용
            df = self._compute_ma(df, period, method, target_col="ma")
                
            results = [
                {
                    "code": stock_code, "date": str(row.date),
                    "close": self._safe_float(row.close), 
                    "ma": self._safe_float(row.ma)
                }
                for row in df.itertuples(index=False)
            ]
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=results)
        except Exception as e:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)

    def _calculate_indicators_full(self, stock_code: str, ohlcv_data: List[Dict]) -> ResCommonResponse:
        """전체 데이터를 받아 지표를 계산하는 내부 메서드 (Vectorized 최적화 버전)"""
        try:
            # 1. DataFrame 변환 (1회 수행)
            df = self._to_dataframe(ohlcv_data)
            if df.empty:
                 return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="데이터 없음", data=None)

            # 2. 지표 계산 (Vectorized operations)
            # MA
            for p in [5, 10, 20, 60, 120]:
                df = self._compute_ma(df, p, "sma", target_col=f"ma{p}")

            # BB (20일, 2.0)
            df = self._compute_bb(df, 20, 2.0, prefix="bb")

            # RS (63일 등락률)
            rs_period = 63
            df['rs'] = df['close'].pct_change(periods=rs_period) * 100

            # 3. 결과 포맷팅 전처리 (빠른 일괄 변환)
            # 3-1. 응답 규격을 맞추기 위해 date 컬럼을 일괄 문자열로 캐스팅
            df['date'] = df['date'].astype(str)
            
            # 3-2. 숫자형 컬럼의 무한대(inf)를 NaN으로 치환 (문자열 컬럼 순회 방지로 속도 극대화)
            num_cols = df.select_dtypes(include=[np.number]).columns
            df[num_cols] = df[num_cols].replace([np.inf, -np.inf], np.nan)
            
            # 3-3. JSON 직렬화를 위해 모든 NaN을 None으로 일괄 치환
            # (Pandas float64 자동 캐스팅 방지를 위해 object 명시적 캐스팅 적용)
            df = df.astype(object).where(pd.notnull(df), None)

            # 4. 결과 포맷팅 (itertuples 활용 최적화)
            indicators = {}
            
            # MA 추출
            for p in [5, 10, 20, 60, 120]:
                ma_key = f'ma{p}'
                indicators[ma_key] = [
                    {"date": str(r.date), "close": r.close, "ma": getattr(r, ma_key)}
                    for r in df.itertuples(index=False)
                ]

            # BB 추출
            indicators["bb"] = [
                {
                    "code": stock_code, "date": str(r.date), "close": r.close,
                    "middle": r.bb_middle, "upper": r.bb_upper, "lower": r.bb_lower
                }
                for r in df.itertuples(index=False)
            ]

            # RS 추출
            indicators["rs"] = [
                {"date": str(r.date), "rs": r.rs}
                for r in df.itertuples(index=False)
            ]

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=indicators)

        except Exception as e:
            logging.getLogger(__name__).exception(f"지표 통합 계산 중 오류: {e}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)
