import logging
import pandas as pd
from typing import List, Dict, Optional
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage
from services.trading_service import TradingService

class IndicatorService:
    """
    기술적 지표 계산을 담당하는 서비스.
    TradingService를 통해 데이터를 조회하고 가공하여 지표 값을 반환합니다.
    """
    def __init__(self, trading_service: TradingService):
        self.trading_service = trading_service

    async def _get_ohlcv_data(self, stock_code: str, candle_type: str, ohlcv_data: Optional[List[Dict]] = None) -> tuple:
        """
        OHLCV 데이터를 가져옵니다. ohlcv_data가 전달되면 API 호출을 생략합니다.
        Returns: (ohlcv_data, error_response) - 성공 시 error_response는 None
        """
        if ohlcv_data is not None:
            return ohlcv_data, None

        resp = await self.trading_service.get_ohlcv(stock_code, period=candle_type)
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
        data, err = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
        if err:
            return err

        if len(data) < period:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(data)} < {period}",
                data=None
            )

        # 2. Pandas DataFrame 변환 및 계산
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
        # 1. OHLCV 데이터 조회
        resp = await self.trading_service.get_ohlcv(stock_code, period=candle_type)

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
        data, err = await self._get_ohlcv_data(stock_code, candle_type, ohlcv_data)
        if err:
            return err

        if len(data) < period:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(data)} < {period}",
                data=None
            )

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

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=results)

        except ValueError as e:
            logging.getLogger(__name__).exception(f"MA 계산 데이터 변환 실패: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.PARSING_ERROR.value, msg1=f"데이터 변환 실패: {str(e)}", data=None)

        except Exception as e:
            logging.getLogger(__name__).exception(f"MA 계산 중 오류: {str(e)}")
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"MA 계산 중 오류: {str(e)}", data=None)
