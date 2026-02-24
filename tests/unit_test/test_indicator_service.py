import pytest
from unittest.mock import AsyncMock, MagicMock
import pandas as pd
from services.indicator_service import IndicatorService
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage

@pytest.fixture
def indicator_service():
    mock_trading_service = AsyncMock()
    return IndicatorService(mock_trading_service), mock_trading_service

@pytest.mark.asyncio
async def test_get_bollinger_bands_success(indicator_service):
    """볼린저 밴드 계산 성공 시나리오"""
    service, mock_ts = indicator_service
    
    # 25일치 데이터 생성 (20일 이동평균 계산 가능하도록)
    data = []
    for i in range(25):
        data.append({
            "date": f"202501{i+1:02d}",
            "close": 10000 + i * 100, # 10000, 10100, ... 선형 증가
            "open": 10000, "high": 10000, "low": 10000, "volume": 100
        })
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data, list)
    assert len(result.data) == 25
    
    last_item = result.data[-1]
    assert last_item.code == "005930"
    assert last_item.date == "20250125"
    # 마지막 데이터 close: 10000 + 24*100 = 12400
    assert last_item.close == 12400.0
    
    # 볼린저 밴드 특성 확인 (상단 > 중단 > 하단)
    assert last_item.upper > last_item.middle
    assert last_item.lower < last_item.middle

@pytest.mark.asyncio
async def test_get_bollinger_bands_not_enough_data(indicator_service):
    """데이터가 부족할 때 (period 미만)"""
    service, mock_ts = indicator_service
    
    # 데이터 10개 (기본 period 20개 필요)
    data = [{"date": "20250101", "close": 10000}] * 10
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930", period=20)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1

@pytest.mark.asyncio
async def test_get_bollinger_bands_api_failure(indicator_service):
    """TradingService API 호출 실패 시"""
    service, mock_ts = indicator_service
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )

    result = await service.get_bollinger_bands("005930")

    assert result.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_bollinger_bands_calculation_error(indicator_service):
    """계산 중 예외 발생 시 (예: 숫자가 아닌 데이터)"""
    service, mock_ts = indicator_service
    
    # 숫자가 아닌 문자열 데이터
    data = [{"date": "20250101", "close": "invalid_number"}] * 25
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")
    
    # pd.to_numeric 변환 실패로 ValueError 발생 -> UNKNOWN_ERROR 반환 예상
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "볼린저 밴드 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_success(indicator_service):
    """RSI 계산 성공 시나리오"""
    service, mock_ts = indicator_service
    
    # 30일치 데이터 생성 (14일 RSI 계산 가능하도록)
    # 지속적인 상승 추세 -> RSI가 높게 나와야 함
    data = []
    for i in range(30):
        data.append({
            "date": f"202501{i+1:02d}",
            "close": 10000 + i * 100, 
            "open": 10000, "high": 10000, "low": 10000, "volume": 100
        })
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data, ResRSI)
    assert result.data.code == "005930"
    # 지속 상승했으므로 RSI는 50보다 커야 함 (실제로는 100에 가까움)
    assert result.data.rsi > 50

@pytest.mark.asyncio
async def test_get_moving_average_sma_success(indicator_service):
    """이동평균선(SMA) 계산 성공 시나리오"""
    service, mock_ts = indicator_service
    
    # 5일치 데이터 생성 (5일 이동평균 계산)
    # 가격: 100, 200, 300, 400, 500
    data = []
    for i in range(5):
        data.append({
            "date": f"2025010{i+1}",
            "close": (i + 1) * 100, 
            "open": 100, "high": 100, "low": 100, "volume": 100
        })
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", period=5, method="sma")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data, list)
    assert len(result.data) == 5
    
    # 마지막 데이터의 MA 값 검증
    # (100+200+300+400+500) / 5 = 300
    last_item = result.data[-1]
    assert isinstance(last_item, ResMovingAverage)
    assert last_item.ma == 300.0
    
    # 첫 번째 데이터는 MA 계산 불가 (None)
    first_item = result.data[0]
    assert first_item.ma is None

@pytest.mark.asyncio
async def test_get_moving_average_ema_success(indicator_service):
    """이동평균선(EMA) 계산 성공 시나리오"""
    service, mock_ts = indicator_service
    
    # 데이터 생성 (가격 일정)
    data = [{"date": f"2025010{i+1}", "close": 10000, "open": 10000, "high": 10000, "low": 10000, "volume": 100} for i in range(10)]
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", period=5, method="ema")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 가격이 일정하므로 EMA도 가격과 동일해야 함
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_moving_average_not_enough_data(indicator_service):
    """데이터 부족 시나리오"""
    service, mock_ts = indicator_service
    
    # 데이터 3개 (period 5 필요)
    data = [{"date": "20250101", "close": 10000}] * 3
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", period=5)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1