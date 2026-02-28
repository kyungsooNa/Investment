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
async def test_get_bollinger_bands_with_preloaded_data(indicator_service):
    """ohlcv_data를 미리 전달하여 API 호출을 건너뛰는지 테스트"""
    service, mock_ts = indicator_service
    
    # 20일치 데이터 생성
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(20)]
    
    # ohlcv_data를 직접 전달
    result = await service.get_bollinger_bands("005930", ohlcv_data=data)

    # API가 호출되지 않았는지 확인
    mock_ts.get_ohlcv.assert_not_called()
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 20
    # 첫 19개 데이터는 MA, std 등이 NaN이므로 middle, upper, lower가 None이어야 함
    assert result.data[0].middle is None
    assert result.data[18].middle is None
    # 마지막 데이터는 값이 있어야 함
    assert result.data[19].middle is not None

@pytest.mark.asyncio
async def test_get_rsi_not_enough_data(indicator_service):
    """RSI 계산에 데이터가 부족한 경우 (period + 1 미만)"""
    service, mock_ts = indicator_service
    
    # 14일 RSI 계산에 10개 데이터만 제공
    data = [{"date": "20250101", "close": 10000}] * 10
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930", period=14)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_api_failure(indicator_service):
    """RSI 계산 시 OHLCV API 호출 실패"""
    service, mock_ts = indicator_service
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )

    result = await service.get_rsi("005930")

    assert result.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_rsi_calculation_error(indicator_service):
    """RSI 계산 중 예외 발생 (예: 숫자가 아닌 데이터)"""
    service, mock_ts = indicator_service
    
    data_invalid = [{"date": f"202501{i+1:02d}", "close": "invalid"} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_invalid
    )
    result = await service.get_rsi("005930")

    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "RSI 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_with_preloaded_data(indicator_service):
    """get_moving_average에 ohlcv_data를 미리 전달하여 API 호출을 건너뛰는지 테스트"""
    service, mock_ts = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": "10000"} for i in range(20)]
    
    result = await service.get_moving_average("005930", ohlcv_data=data)

    mock_ts.get_ohlcv.assert_not_called()
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 20
    # close가 문자열이어도 내부적으로 pd.to_numeric으로 처리되는지 확인
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_moving_average_calculation_error(indicator_service):
    """get_moving_average 계산 중 예외 발생"""
    service, mock_ts = indicator_service
    
    data_invalid = [{"date": f"202501{i+1:02d}", "close": "invalid"} for i in range(20)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_invalid
    )
    
    result = await service.get_moving_average("005930")

    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "MA 계산 중 오류" in result.msg1

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