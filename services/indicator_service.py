import pandas as pd
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand
from services.trading_service import TradingService

class IndicatorService:
    """
    기술적 지표 계산을 담당하는 서비스.
    TradingService를 통해 데이터를 조회하고 가공하여 지표 값을 반환합니다.
    """
    def __init__(self, trading_service: TradingService):
        self.trading_service = trading_service

    async def get_bollinger_bands(self, stock_code: str, period: int = 20, std_dev: float = 2.0, candle_type: str = "D") -> ResCommonResponse:
        """
        특정 종목의 볼린저 밴드(상단, 중단, 하단)를 계산하여 반환합니다.
        
        :param stock_code: 종목코드
        :param period: 이동평균 기간 (기본 20)
        :param std_dev: 표준편차 승수 (기본 2.0)
        :param candle_type: 봉 타입 ('D':일봉, 'W':주봉, 'M':월봉 등)
        """
        # 1. OHLCV 데이터 조회
        # TradingService.get_ohlcv는 기본적으로 충분한 기간(약 1년)을 조회합니다.
        resp = await self.trading_service.get_ohlcv(stock_code, period=candle_type)
        
        if resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return resp
        
        ohlcv_data = resp.data # List[dict]
        
        if len(ohlcv_data) < period:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"데이터 부족: {len(ohlcv_data)} < {period}",
                data=None
            )

        # 2. Pandas DataFrame 변환 및 계산
        try:
            df = pd.DataFrame(ohlcv_data)
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

            # 최신 데이터 (마지막 행)
            latest = df.iloc[-1]
            
            # NaN 체크 (데이터가 부족하여 계산되지 않은 경우)
            if pd.isna(latest['MB']):
                 return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="계산 불가 (데이터 부족)", data=None)

            result = ResBollingerBand(code=stock_code, date=str(latest['date']), close=float(latest['close']), middle=float(latest['MB']), upper=float(latest['UB']), lower=float(latest['LB']))
            
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=result)

        except Exception as e:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=f"볼린저 밴드 계산 중 오류: {str(e)}", data=None)