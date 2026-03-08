import logging
import pandas as pd
import numpy as np
import math
import time
from typing import List, Dict, Optional, TYPE_CHECKING
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage, ResRelativeStrength

if TYPE_CHECKING:
    from services.stock_query_service import StockQueryService

class IndicatorService:
    """
    기술적 지표 계산을 담당하는 서비스.
    StockQueryService를 통해 데이터를 조회하고 가공하여 지표 값을 반환합니다.
    """
    def __init__(self, stock_query_service: Optional['StockQueryService'] = None):
        self.stock_query_service = stock_query_service

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

    async def get_bollinger_bands(self, stock_code: str, period: int = 20, std_dev: float = 2.0,
                                  candle_type: str = "D", ohlcv_data: Optional[List[Dict]] = None) -> ResCommonResponse:
        """
        특정 종목의 볼린저 밴드(상단, 중단, 하단)를 계산하여 반환합니다.

        :param stock_code: 종목코드
        :param period: 이동평균 기간 (기본 20)
        :param std_dev: 표준편차 승수 (기본 2.0)
        :param candle_type: 봉 타입 ('D':일봉, 'W':주봉, 'M':월봉 등)
        :param ohlcv_data: 미리 조회된 OHLCV 데이터 (전달 시 API 호출 생략)
        """
        start_time = time.time()
        data, err = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
        data_end_time = time.time()
        if err:
            return err

        if len(data) < period:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(data)} < {period}",
                data=None
            )

        # 2. Pandas DataFrame 변환 및 계산
        calc_start_time = time.time()
        try:
            df = pd.DataFrame(data)
            # close 컬럼이 문자열일 수 있으므로 숫자형으로 변환
            if df['close'].dtype == object:
                 df['close'] = pd.to_numeric(df['close'])

            # 중심선 (MB) = n일 이동평균
            df['MB'] = df['close'].rolling(window=period).mean()

            # 표준편차 (std)
            df['std'] = df['close'].rolling(window=period).std()

            # 상단밴드 (UB) = MB + (std * k)
            df['UB'] = df['MB'] + (df['std'] * std_dev)

            # 하단밴드 (LB) = MB - (std * k)
            df['LB'] = df['MB'] - (df['std'] * std_dev)

            results = []
            for i in range(len(df)):
                row = df.iloc[i]
                # NaN 처리 (데이터 부족 구간)
                mb = float(row['MB']) if not pd.isna(row['MB']) else None
                ub = float(row['UB']) if not pd.isna(row['UB']) else None
                lb = float(row['LB']) if not pd.isna(row['LB']) else None

                results.append(ResBollingerBand(
                    code=stock_code, date=str(row['date']), close=float(row['close']),
                    middle=mb, upper=ub, lower=lb
                ))

            calc_end_time = time.time()
            print(f"[Performance] IndicatorService.get_bollinger_bands({stock_code}): total={calc_end_time - start_time:.4f}s (data={data_end_time - start_time:.4f}s, calc={calc_end_time - calc_start_time:.4f}s)")

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=results)

        except ValueError as e:
            logging.getLogger(__name__).exception(f"볼린저 밴드 계산 데이터 변환 실패: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=f"데이터 변환 실패: {str(e)}", data=None)

        except Exception as e:
            logging.getLogger(__name__).exception(f"볼린저 밴드 계산 중 오류: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"볼린저 밴드 계산 중 오류: {str(e)}", data=None)

    async def get_rsi(self, stock_code: str, period: int = 14, candle_type: str = "D") -> ResCommonResponse:
        """
        특정 종목의 RSI(상대강도지수)를 계산하여 반환합니다.

        :param stock_code: 종목코드
        :param period: RSI 기간 (기본 14)
        :param candle_type: 봉 타입 ('D':일봉, 'W':주봉, 'M':월봉 등)
        """
        start_time = time.time()
        # 1. OHLCV 데이터 조회
        if not self.stock_query_service:
            return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="StockQueryService not initialized", data=None)

        resp = await self.stock_query_service.get_ohlcv(stock_code, period=candle_type)
        data_end_time = time.time()

        if resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return resp

        ohlcv_data = resp.data

        # 전일 대비 계산을 위해 최소 period + 1개 데이터 필요
        if len(ohlcv_data) < period + 1:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(ohlcv_data)} < {period + 1}",
                data=None
            )

        calc_start_time = time.time()
        try:
            df = pd.DataFrame(ohlcv_data)
            if df['close'].dtype == object:
                 df['close'] = pd.to_numeric(df['close'])

            # 전일 대비 변동분
            delta = df['close'].diff()

            # 상승분(U)과 하락분(D) 분리
            u = delta.clip(lower=0)
            d = -1 * delta.clip(upper=0)

            # Wilder's Smoothing (alpha=1/period) 적용
            au = u.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
            ad = d.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

            # RS 및 RSI 계산
            rs = au / ad
            rsi = 100 - (100 / (1 + rs))

            latest = df.iloc[-1]
            latest_rsi = rsi.iloc[-1]

            if pd.isna(latest_rsi):
                 return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="계산 불가 (데이터 부족)", data=None)

            result = ResRSI(code=stock_code, date=str(latest['date']), close=float(latest['close']), rsi=float(latest_rsi))
            
            calc_end_time = time.time()
            print(f"[Performance] IndicatorService.get_rsi({stock_code}): total={calc_end_time - start_time:.4f}s (data={data_end_time - start_time:.4f}s, calc={calc_end_time - calc_start_time:.4f}s)")
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=result)

        except ValueError as e:
            logging.getLogger(__name__).exception(f"RSI 계산 데이터 변환 실패: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=f"데이터 변환 실패: {str(e)}", data=None)

        except Exception as e:
            logging.getLogger(__name__).exception(f"RSI 계산 중 오류: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"RSI 계산 중 오류: {str(e)}", data=None)

    async def get_moving_average(self, stock_code: str, period: int = 20, method: str = "sma",
                                  candle_type: str = "D", ohlcv_data: Optional[List[Dict]] = None) -> ResCommonResponse:
        """
        특정 종목의 이동평균선(MA)을 계산하여 반환합니다.

        :param stock_code: 종목코드
        :param period: 기간 (기본 20)
        :param method: 방식 ('sma': 단순, 'ema': 지수)
        :param candle_type: 봉 타입 ('D':일봉, 'W':주봉, 'M':월봉 등)
        :param ohlcv_data: 미리 조회된 OHLCV 데이터 (전달 시 API 호출 생략)
        """
        start_time = time.time()
        data, err = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
        data_end_time = time.time()
        if err:
            return err

        if len(data) < period:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(data)} < {period}",
                data=None
            )

        calc_start_time = time.time()
        try:
            df = pd.DataFrame(data)
            if df['close'].dtype == object:
                 df['close'] = pd.to_numeric(df['close'])

            if method.lower() == "ema":
                ma_series = df['close'].ewm(span=period, adjust=False).mean()
            else: # sma
                ma_series = df['close'].rolling(window=period).mean()

            results = []
            for i in range(len(df)):
                val = ma_series.iloc[i]
                ma_val = float(val) if not pd.isna(val) else None

                results.append(ResMovingAverage(
                    code=stock_code,
                    date=str(df.iloc[i]['date']),
                    close=float(df.iloc[i]['close']),
                    ma=ma_val
                ))

            calc_end_time = time.time()
            print(f"[Performance] IndicatorService.get_moving_average({stock_code}, p={period}): total={calc_end_time - start_time:.4f}s (data={data_end_time - start_time:.4f}s, calc={calc_end_time - calc_start_time:.4f}s)")

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=results)

        except ValueError as e:
            logging.getLogger(__name__).exception(f"MA 계산 데이터 변환 실패: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=f"데이터 변환 실패: {str(e)}", data=None)

        except Exception as e:
            logging.getLogger(__name__).exception(f"MA 계산 중 오류: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"MA 계산 중 오류: {str(e)}", data=None)

    async def get_relative_strength(self, stock_code: str, period_days: int = 63,
                                     candle_type: str = "D",
                                     ohlcv_data: Optional[List[Dict]] = None) -> ResCommonResponse:
        """N일 수익률(상대강도 원시값)을 계산하여 반환합니다.

        오닐의 RS 지수는 전 상장 종목 대비 상대 순위이나,
        API 제약상 호출 측에서 후보군 내 퍼센타일을 별도로 산출해야 합니다.
        이 메서드는 개별 종목의 절대 N일 수익률만 반환합니다.

        :param stock_code: 종목코드
        :param period_days: 수익률 계산 기간 (기본 63 ≈ 3개월)
        :param candle_type: 봉 타입 ('D':일봉)
        :param ohlcv_data: 미리 조회된 OHLCV 데이터 (전달 시 API 호출 생략)
        """
        start_time = time.time()
        data, err = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
        data_end_time = time.time()
        if err:
            return err

        if len(data) < period_days:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(data)} < {period_days}",
                data=None
            )

        calc_start_time = time.time()
        try:
            df = pd.DataFrame(data)
            if df['close'].dtype == object:
                df['close'] = pd.to_numeric(df['close'])

            recent_close = float(df['close'].iloc[-1])
            past_close = float(df['close'].iloc[-period_days])

            if past_close <= 0:
                return ResCommonResponse(
                    rt_cd=ErrorCode.EMPTY_VALUES.value,
                    msg1=f"과거 종가가 0 이하: {past_close}",
                    data=None
                )

            return_pct = ((recent_close - past_close) / past_close) * 100

            result = ResRelativeStrength(
                code=stock_code,
                date=str(df['date'].iloc[-1]),
                return_pct=round(return_pct, 2),
            )
            
            calc_end_time = time.time()
            print(f"[Performance] IndicatorService.get_relative_strength({stock_code}): total={calc_end_time - start_time:.4f}s (data={data_end_time - start_time:.4f}s, calc={calc_end_time - calc_start_time:.4f}s)")
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=result)

        except Exception as e:
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"상대강도 계산 중 오류: {str(e)}",
                data=None
            )

    async def get_chart_indicators(self, stock_code: str, ohlcv_data: List[Dict]) -> ResCommonResponse:
        """
        차트 렌더링용 지표(MA 5/10/20/60/120, BB, RS)를 한 번에 계산하여 반환합니다.
        DataFrame 변환 오버헤드를 최소화하기 위함입니다.
        """
        start_time = time.time()
        try:
            # 1. DataFrame 변환 (1회 수행)
            df = pd.DataFrame(ohlcv_data)
            if df.empty:
                 return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="데이터 없음", data=None)
                 
            if df['close'].dtype == object:
                df['close'] = pd.to_numeric(df['close'])

            # 2. 지표 계산 (Vectorized operations)
            # MA
            for p in [5, 10, 20, 60, 120]:
                df[f'ma{p}'] = df['close'].rolling(window=p).mean()

            # BB (20일, 2.0)
            period = 20
            std_dev = 2.0
            mb = df['close'].rolling(window=period).mean()
            std = df['close'].rolling(window=period).std()
            df['bb_upper'] = mb + (std * std_dev)
            df['bb_lower'] = mb - (std * std_dev)
            df['bb_middle'] = mb

            # RS (63일 등락률)
            rs_period = 63
            df['rs'] = df['close'].pct_change(periods=rs_period) * 100

            # 3. 결과 포맷팅
            # NaN 및 Inf 값은 JSON 직렬화 시 문제가 되므로 안전하게 변환
            def _safe_float(val):
                if val is None:
                    return None
                try:
                    f = float(val)
                    if math.isnan(f) or math.isinf(f):
                        return None
                    return f
                except (ValueError, TypeError):
                    return None

            indicators = {}
            rows = list(df.itertuples(index=False))
            
            for p in [5, 10, 20, 60, 120]:
                ma_key = f'ma{p}'
                indicators[ma_key] = [{"date": str(r.date), "close": _safe_float(r.close), "ma": _safe_float(getattr(r, ma_key, None))} for r in rows]

            indicators["bb"] = [
                {
                    "code": stock_code, "date": str(r.date), "close": _safe_float(r.close),
                    "middle": _safe_float(getattr(r, 'bb_middle', None)),
                    "upper": _safe_float(getattr(r, 'bb_upper', None)),
                    "lower": _safe_float(getattr(r, 'bb_lower', None))
                } for r in rows
            ]

            indicators["rs"] = [
                {"date": str(r.date), "rs": _safe_float(getattr(r, 'rs', None))} for r in rows
            ]

            print(f"[Performance] IndicatorService.get_chart_indicators({stock_code}): {time.time() - start_time:.4f}s")
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=indicators)

        except Exception as e:
            logging.getLogger(__name__).exception(f"지표 통합 계산 중 오류: {e}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)
