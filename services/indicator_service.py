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
            full_result = calc_func(stock_code, data, *calc_args)
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공(NoCache)", data=full_result)

        # 2. 확정 데이터(어제까지)와 당일 데이터 분리
        confirmed_data = data[:-1]
        confirmed_last_date = str(confirmed_data[-1]['date'])
        
        # 캐시 키 생성 (지표명_종목코드_마지막확정일자)
        cache_key = f"{indicator_name}_{stock_code}_{confirmed_last_date}"

        # 3. 캐시 조회
        cached_result = self.cache_store.get(cache_key)

        # 4. 캐시 미스: 확정 데이터 전체에 대해 지표 계산 후 캐시 저장
        if not cached_result:
            cached_result = calc_func(stock_code, confirmed_data, *calc_args)
            self.cache_store.set(cache_key, cached_result) # 필요시 TTL 지정

        # 5. 당일 증분 계산
        # 계산에 필요한 최소한의 과거 데이터만 슬라이싱 (여유분으로 +5 설정)
        slice_size = lookback_period + 5
        partial_data = data[-slice_size:]
        
        # 부분 데이터로 계산 (가장 마지막 요소가 오늘자 지표 값임)
        partial_result = calc_func(stock_code, partial_data, *calc_args)
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
        data, err_resp = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
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
            # 마지막 데이터 날짜 (캐시 키용)
            confirmed_last_date = str(confirmed_data[-1]['date'])
            
            cache_key = f"indicators_chart_{stock_code}_{confirmed_last_date}"
            
            # 2. 캐시 조회
            raw_cache = self.cache_store.get_raw(cache_key)
            cached_wrapper = None
            if raw_cache and isinstance(raw_cache, tuple):
                cached_wrapper, _ = raw_cache
            
            past_indicators = None
            if cached_wrapper:
                past_indicators = cached_wrapper.get('data')

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

    @staticmethod
    def _to_dataframe(data: Union[List[Dict], pd.DataFrame]) -> pd.DataFrame:
        """List[Dict] 또는 기존 DataFrame을 받아 pandas DataFrame으로 통일 및 데이터 전처리를 수행합니다."""
        if isinstance(data, pd.DataFrame):
            df = data.copy()
        else:
            # Dict-of-lists 방식이 List[Dict]보다 DataFrame 생성 속도가 빠름
            df = pd.DataFrame({k: [d[k] for d in data] for k in data[0]}) if data else pd.DataFrame()
            
        if not df.empty and 'close' in df.columns and df['close'].dtype == object:
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
        # [추가] inf 값을 NaN으로 치환하여 이후 연산(rolling, ewm 등)의 예외를 원천 방지
        if not df.empty:
            # 숫자형 컬럼에 대해서만 replace 수행 (속도 극대화)
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)

        return df

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
        std = df['close'].rolling(window=period).std()
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

    def _calculate_bollinger_bands_full(self, stock_code, data, period, std_dev) -> ResCommonResponse:
        """볼린저 밴드 전체 계산 (내부용)"""
        try:
            df = self._to_dataframe(data)
            
            df['MB'] = df['close'].rolling(window=period).mean()
            df['std'] = df['close'].rolling(window=period).std()
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

            # 4. 결과 포맷팅 (Pandas to_dict 활용 - 파이썬 반복문 0번)
            indicators = {}
            
            # MA 추출
            for p in [5, 10, 20, 60, 120]:
                ma_key = f'ma{p}'
                indicators[ma_key] = df[['date', 'close', ma_key]].rename(
                    columns={ma_key: 'ma'}
                ).to_dict('records')

            # BB 추출 (이름 매핑 및 code 컬럼 동적 추가)
            indicators["bb"] = df[['date', 'close', 'bb_middle', 'bb_upper', 'bb_lower']].rename(
                columns={'bb_middle': 'middle', 'bb_upper': 'upper', 'bb_lower': 'lower'}
            ).assign(code=stock_code)[['code', 'date', 'close', 'middle', 'upper', 'lower']].to_dict('records')

            # RS 추출
            indicators["rs"] = df[['date', 'rs']].to_dict('records')

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=indicators)

        except Exception as e:
            logging.getLogger(__name__).exception(f"지표 통합 계산 중 오류: {e}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)
