import pytest
from unittest.mock import AsyncMock, MagicMock
import pandas as pd
from services.indicator_service import IndicatorService
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand

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
    assert isinstance(result.data, ResBollingerBand)
    assert result.data.code == "005930"
    assert result.data.date == "20250125"
    # 마지막 데이터 close: 10000 + 24*100 = 12400
    assert result.data.close == 12400.0
    
    # 볼린저 밴드 특성 확인 (상단 > 중단 > 하단)
    assert result.data.middle > 0
    assert result.data.upper > result.data.middle
    assert result.data.lower < result.data.middle

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