import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from services.indicator_service import IndicatorService
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage, ResRelativeStrength
from core.cache.cache_manager import CacheManager

@pytest.fixture
def indicator_service():
    mock_sqs = AsyncMock()
    return IndicatorService(mock_sqs), mock_sqs

@pytest.mark.asyncio
async def test_get_bollinger_bands_success(indicator_service):
    """볼린저 밴드 계산 성공 시나리오"""
    service, mock_sqs = indicator_service
    
    # 25일치 데이터 생성 (20일 이동평균 계산 가능하도록)
    data = []
    for i in range(25):
        data.append({
            "date": f"202501{i+1:02d}",
            "close": 10000 + i * 100, # 10000, 10100, ... 선형 증가
            "open": 10000, "high": 10000, "low": 10000, "volume": 100
        })
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_sqs = indicator_service
    
    # 데이터 10개 (기본 period 20개 필요)
    data = [{"date": "20250101", "close": 10000}] * 10
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930", period=20)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1

@pytest.mark.asyncio
async def test_get_bollinger_bands_api_failure(indicator_service):
    """TradingService API 호출 실패 시"""
    service, mock_sqs = indicator_service
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )

    result = await service.get_bollinger_bands("005930")

    assert result.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_bollinger_bands_calculation_error(indicator_service):
    """계산 중 예외 발생 시 (예: 숫자가 아닌 데이터)"""
    service, mock_sqs = indicator_service
    
    # 숫자가 아닌 문자열 데이터
    data = [{"date": "20250101", "close": "invalid_number"}] * 25
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        result = await service.get_bollinger_bands("005930")
        
        # pd.to_numeric 변환 실패로 ValueError 발생 -> PARSING_ERROR 반환 예상
        assert result.rt_cd == ErrorCode.PARSING_ERROR.value
        assert "데이터 변환 실패" in result.msg1
        mock_logger.exception.assert_called_once()

@pytest.mark.asyncio
async def test_get_rsi_success(indicator_service):
    """RSI 계산 성공 시나리오"""
    service, mock_sqs = indicator_service
    
    # 30일치 데이터 생성 (14일 RSI 계산 가능하도록)
    # 지속적인 상승 추세 -> RSI가 높게 나와야 함
    data = []
    for i in range(30):
        data.append({
            "date": f"202501{i+1:02d}",
            "close": 10000 + i * 100, 
            "open": 10000, "high": 10000, "low": 10000, "volume": 100
        })
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_sqs = indicator_service
    
    # 5일치 데이터 생성 (5일 이동평균 계산)
    # 가격: 100, 200, 300, 400, 500
    data = []
    for i in range(5):
        data.append({
            "date": f"2025010{i+1}",
            "close": (i + 1) * 100, 
            "open": 100, "high": 100, "low": 100, "volume": 100
        })
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_sqs = indicator_service
    
    # 20일치 데이터 생성
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(20)]
    
    # ohlcv_data를 직접 전달
    result = await service.get_bollinger_bands("005930", ohlcv_data=data)

    # API가 호출되지 않았는지 확인
    mock_sqs.get_ohlcv.assert_not_called()
    
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
    service, mock_sqs = indicator_service
    
    # 14일 RSI 계산에 10개 데이터만 제공
    data = [{"date": "20250101", "close": 10000}] * 10
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930", period=14)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_api_failure(indicator_service):
    """RSI 계산 시 OHLCV API 호출 실패"""
    service, mock_sqs = indicator_service
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )

    result = await service.get_rsi("005930")

    assert result.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_rsi_calculation_error(indicator_service):
    """RSI 계산 중 예외 발생 (예: 숫자가 아닌 데이터)"""
    service, mock_sqs = indicator_service
    
    data_invalid = [{"date": f"202501{i+1:02d}", "close": "invalid"} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_invalid
    )
    
    with patch("services.indicator_service.logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        result = await service.get_rsi("005930")

        assert result.rt_cd == ErrorCode.PARSING_ERROR.value
        assert "데이터 변환 실패" in result.msg1
        mock_logger.exception.assert_called_once()

@pytest.mark.asyncio
async def test_get_moving_average_with_preloaded_data(indicator_service):
    """get_moving_average에 ohlcv_data를 미리 전달하여 API 호출을 건너뛰는지 테스트"""
    service, mock_sqs = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": "10000"} for i in range(20)]
    
    result = await service.get_moving_average("005930", ohlcv_data=data)

    mock_sqs.get_ohlcv.assert_not_called()
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 20
    # close가 문자열이어도 내부적으로 pd.to_numeric으로 처리되는지 확인
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_moving_average_calculation_error(indicator_service):
    """get_moving_average 계산 중 예외 발생"""
    service, mock_sqs = indicator_service
    
    data_invalid = [{"date": f"202501{i+1:02d}", "close": "invalid"} for i in range(20)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_invalid
    )
    
    with patch("services.indicator_service.logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        result = await service.get_moving_average("005930")

        assert result.rt_cd == ErrorCode.PARSING_ERROR.value
        assert "데이터 변환 실패" in result.msg1
        mock_logger.exception.assert_called_once()

@pytest.mark.asyncio
async def test_get_moving_average_ema_success(indicator_service):
    """이동평균선(EMA) 계산 성공 시나리오"""
    service, mock_sqs = indicator_service
    
    # 데이터 생성 (가격 일정)
    data = [{"date": f"2025010{i+1}", "close": 10000, "open": 10000, "high": 10000, "low": 10000, "volume": 100} for i in range(10)]
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", period=5, method="ema")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 가격이 일정하므로 EMA도 가격과 동일해야 함
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_moving_average_not_enough_data(indicator_service):
    """데이터 부족 시나리오"""
    service, mock_sqs = indicator_service
    
    # 데이터 3개 (period 5 필요)
    data = [{"date": "20250101", "close": 10000}] * 3
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", period=5)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1


# ═══════════════════════════════════════════════════════
# RS (상대강도) 테스트
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_relative_strength_success(indicator_service):
    """3개월 수익률 정상 계산"""
    service, mock_sqs = indicator_service

    # 70일치 데이터 (period_days=63 충분)
    data = []
    for i in range(70):
        data.append({
            "date": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "close": 10000 + i * 50,  # 10000 → 13450 선형 상승
            "open": 10000, "high": 10000, "low": 10000, "volume": 100,
        })

    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data is not None
    assert isinstance(result.data, ResRelativeStrength)
    assert result.data.code == "005930"
    # 최근종가=data[-1].close, 63일전종가=data[-63].close
    # 수익률 ≈ 29~31% (인덱싱에 따라 약간 다를 수 있음)
    assert result.data.return_pct > 25.0
    assert result.data.return_pct < 35.0


@pytest.mark.asyncio
async def test_get_relative_strength_insufficient_data(indicator_service):
    """데이터 부족 시 EMPTY_VALUES 반환"""
    service, mock_sqs = indicator_service

    # 30일치만 (period_days=63 필요)
    data = [{"date": f"2025010{i+1}", "close": 10000 + i * 100,
             "open": 10000, "high": 10000, "low": 10000, "volume": 100}
            for i in range(30)]

    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "데이터 부족" in result.msg1


@pytest.mark.asyncio
async def test_get_relative_strength_with_ohlcv_data_no_api_call(indicator_service):
    """ohlcv_data 직접 전달 시 API 호출 안함"""
    service, mock_sqs = indicator_service

    data = [{"date": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
             "close": 10000, "open": 10000, "high": 10000, "low": 10000, "volume": 100}
            for i in range(70)]

    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.return_pct == 0.0  # 종가 일정 → 수익률 0%
    mock_sqs.get_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_get_relative_strength_zero_past_close(indicator_service):
    """과거 종가가 0이면 EMPTY_VALUES 반환"""
    service, mock_sqs = indicator_service

    data = [{"date": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
             "close": 0 if i < 10 else 10000,
             "open": 10000, "high": 10000, "low": 10000, "volume": 100}
            for i in range(70)]

    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)

    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value


# ═══════════════════════════════════════════════════════
# 추가 커버리지 테스트 (Edge Cases & Helper Methods)
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_ohlcv_data_direct_failure(indicator_service):
    """_get_ohlcv_data 내부 메서드 직접 테스트: API 실패 시"""
    service, mock_sqs = indicator_service
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )
    
    data, err = await service._get_ohlcv_data("005930", "D")
    assert data is None
    assert err.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_bollinger_bands_string_conversion_success(indicator_service):
    """볼린저 밴드: 문자열로 된 숫자 데이터가 정상적으로 변환되어 계산되는지 테스트"""
    service, mock_sqs = indicator_service
    
    # 문자열 데이터
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 100)} for i in range(25)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data[-1].close == 12400.0 # float로 변환됨 확인

@pytest.mark.asyncio
async def test_get_rsi_string_conversion_success(indicator_service):
    """RSI: 문자열 데이터 변환 성공 테스트"""
    service, mock_sqs = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 100)} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data.rsi, float)

@pytest.mark.asyncio
async def test_get_rsi_nan_result(indicator_service):
    """RSI: 계산 결과가 NaN인 경우 (데이터 값 문제)"""
    service, mock_sqs = indicator_service
    
    # 데이터 개수는 충분하지만 값이 모두 None인 경우 -> 결과 NaN
    data_all_nan = [{"date": f"202501{i+1:02d}", "close": None} for i in range(15)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_all_nan
    )
    
    result = await service.get_rsi("005930", period=14)
    
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "계산 불가" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_unknown_method(indicator_service):
    """이동평균: 알 수 없는 method는 SMA로 처리"""
    service, mock_sqs = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(20)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", method="unknown_method")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_relative_strength_generic_exception(indicator_service):
    """상대강도: 계산 중 알 수 없는 예외 발생 시 처리"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "close": 10000}] * 70
    
    # pandas.DataFrame 생성 시 예외 발생 유도
    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_relative_strength("005930", ohlcv_data=data)
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "상대강도 계산 중 오류" in result.msg1

# ════════════════════════════════════════════════════════════════
# 추가된 테스트 케이스 (Coverage 향상)
# ════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_bollinger_bands_unknown_exception(indicator_service):
    """볼린저 밴드: 알 수 없는 예외 발생 시 UNKNOWN_ERROR 반환"""
    service, mock_sqs = indicator_service
    
    # 데이터는 정상이지만 내부 로직에서 예외 발생 유도
    data = [{"date": "20250101", "close": 10000}] * 25
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_bollinger_bands("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "볼린저 밴드 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_unknown_exception(indicator_service):
    """RSI: 알 수 없는 예외 발생 시 UNKNOWN_ERROR 반환"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "close": 10000}] * 30
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_rsi("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "RSI 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_unknown_exception(indicator_service):
    """이동평균: 알 수 없는 예외 발생 시 UNKNOWN_ERROR 반환"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "close": 10000}] * 25
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_moving_average("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "MA 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_relative_strength_string_conversion(indicator_service):
    """상대강도: 문자열 데이터 변환 성공 테스트"""
    service, mock_sqs = indicator_service
    
    # 문자열 데이터 (period_days=63 이므로 충분한 데이터 생성)
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 10)} for i in range(70)]
    
    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data.return_pct, float)

@pytest.mark.asyncio
async def test_get_ohlcv_data_success_but_no_data(indicator_service):
    """_get_ohlcv_data: API 성공이지만 데이터가 없는(빈 리스트) 경우"""
    service, mock_sqs = indicator_service
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
    )
    
    data, err = await service._get_ohlcv_data("005930", "D")
    
    # data가 None이고 err가 반환되어야 함
    assert data is None
    assert err.rt_cd == ErrorCode.SUCCESS.value
    assert err.data == []

@pytest.mark.asyncio
async def test_get_rsi_success_but_no_data(indicator_service):
    """RSI: API 호출은 성공했으나 데이터가 비어있는 경우"""
    service, mock_sqs = indicator_service
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
    )

    result = await service.get_rsi("005930")

    # resp를 그대로 반환하므로 SUCCESS, data=[] 이어야 함
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == []

@pytest.mark.asyncio
async def test_get_bollinger_bands_missing_column(indicator_service):
    """볼린저 밴드: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_sqs = indicator_service
    
    # 'close' 키가 없는 데이터
    data = [{"date": "20250101", "open": 10000}] * 25
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "볼린저 밴드 계산 중 오류" in result.msg1
    # KeyError 메시지에 컬럼명이 포함됨 (pandas 버전에 따라 메시지가 다를 수 있으므로 'close' 포함 여부만 확인)
    assert "'close'" in result.msg1 or "close" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_missing_column(indicator_service):
    """RSI: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 30
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "RSI 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_missing_column(indicator_service):
    """이동평균: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 25
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "MA 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_relative_strength_missing_column(indicator_service):
    """상대강도: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_sqs = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 70
    
    result = await service.get_relative_strength("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "상대강도 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_constant_price(indicator_service):
    """RSI: 가격이 일정할 때 (변동폭 0 -> RSI NaN) 처리 검증"""
    service, mock_sqs = indicator_service
    
    # 가격이 10000원으로 일정
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")

    # RSI가 NaN이므로 EMPTY_VALUES 반환 예상
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "계산 불가" in result.msg1

@pytest.mark.asyncio
async def test_get_bollinger_bands_constant_price(indicator_service):
    """볼린저 밴드: 가격이 일정할 때 (표준편차 0) 검증"""
    service, mock_sqs = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(25)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    last_item = result.data[-1]
    # 표준편차가 0이므로 상단/중단/하단이 모두 같아야 함
    assert last_item.middle == 10000.0
    assert last_item.upper == 10000.0
    assert last_item.lower == 10000.0

@pytest.mark.asyncio
async def test_get_moving_average_ema_case_insensitive(indicator_service):
    """이동평균: method 대소문자 구분 없이 EMA 처리 확인"""
    service, mock_sqs = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(10)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    # "EMA" 대문자로 호출
    result = await service.get_moving_average("005930", period=5, method="EMA")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    # EMA 로직을 탔다면 결과가 나와야 함
    assert result.data[-1].ma == 10000.0


# ═══════════════════════════════════════════════════════
# 캐싱 및 차트 지표(Chart Indicators) 테스트
# ═══════════════════════════════════════════════════════

@pytest.fixture
def indicator_service_with_cache():
    mock_sqs = AsyncMock()
    mock_cache = MagicMock(spec=CacheManager)
    return IndicatorService(mock_sqs, cache_manager=mock_cache), mock_sqs, mock_cache

@pytest.mark.asyncio
async def test_service_without_sqs():
    """StockQueryService 없이 초기화된 경우 에러 처리 테스트"""
    service = IndicatorService(stock_query_service=None)
    
    # _get_ohlcv_data 호출 (get_bollinger_bands 등에서 사용)
    result = await service.get_bollinger_bands("005930")
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "StockQueryService not initialized" in result.msg1

    # get_rsi 호출 (직접 체크함)
    result = await service.get_rsi("005930")
    assert result.rt_cd == ErrorCode.API_ERROR.value
    assert "StockQueryService not initialized" in result.msg1

@pytest.mark.asyncio
async def test_get_chart_indicators_no_cache_manager(indicator_service):
    """캐시 매니저 없이 차트 지표 계산 (전체 계산)"""
    service, mock_sqs = indicator_service # cache_manager is None
    
    # 150일치 데이터 (충분한 데이터)
    data = [{"date": f"202501{i+1:03d}", "close": 10000 + i * 10} for i in range(150)]
    
    # ohlcv_data 직접 전달
    result = await service.get_chart_indicators("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    indicators = result.data
    
    # 키 확인
    assert "ma5" in indicators
    assert "ma120" in indicators
    assert "bb" in indicators
    assert "rs" in indicators
    
    # 데이터 길이 확인
    assert len(indicators["ma5"]) == 150
    assert len(indicators["bb"]) == 150

@pytest.mark.asyncio
async def test_get_chart_indicators_insufficient_data(indicator_service):
    """데이터 부족 시 차트 지표 계산"""
    service, mock_sqs = indicator_service
    
    # 100개 (130개 미만)
    data = [{"date": f"202501{i+1:03d}", "close": 10000} for i in range(100)]
    
    result = await service.get_chart_indicators("005930", ohlcv_data=data)
    
    # 데이터가 적어도 계산은 수행함 (캐싱만 안함)
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data["ma5"]) == 100

@pytest.mark.asyncio
async def test_get_chart_indicators_caching_miss(indicator_service_with_cache):
    """차트 지표: 캐시 미스 -> 전체 계산 및 저장"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 140개 데이터
    data = [{"date": f"202501{i+1:03d}", "close": 10000 + i} for i in range(140)]
    
    # 캐시 미스 설정
    mock_cache.get_raw.return_value = None
    
    result = await service.get_chart_indicators("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert "indicators_chart_005930" in args[0] # key check
    assert kwargs.get("save_to_file") is True

@pytest.mark.asyncio
async def test_get_chart_indicators_caching_hit(indicator_service_with_cache):
    """차트 지표: 캐시 히트 -> 증분 계산 및 병합"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 140개 데이터 (0~139)
    full_data = [{"date": f"202501{i+1:03d}", "close": 10000 + i} for i in range(140)]
    
    # 캐시된 데이터 (과거 139개에 대한 지표 결과)
    cached_indicators = {
        "ma5": [{"date": d["date"], "ma": 10000.0} for d in full_data[:-1]],
        "bb": [], "rs": []
    }
    
    # get_raw 리턴: (wrapper, metadata)
    mock_cache.get_raw.return_value = ({"data": cached_indicators}, None)
    
    result = await service.get_chart_indicators("005930", ohlcv_data=full_data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 결과 데이터 길이 = 140개 (캐시 139 + 오늘 1)
    assert len(result.data["ma5"]) == 140
    mock_cache.set.assert_not_called()

@pytest.mark.asyncio
async def test_get_chart_indicators_cache_exception(indicator_service_with_cache):
    """차트 지표: 캐싱 로직 중 예외 발생 시 전체 재계산 fallback"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    data = [{"date": f"202501{i+1:03d}", "close": 10000} for i in range(140)]
    
    # 캐시 조회 중 예외 발생
    mock_cache.get_raw.side_effect = Exception("Cache Error")
    
    # 예외가 발생해도 전체 계산으로 성공해야 함
    result = await service.get_chart_indicators("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data["ma5"]) == 140

@pytest.mark.asyncio
async def test_get_bollinger_bands_caching_hit(indicator_service_with_cache):
    """볼린저 밴드: 캐시 히트 시나리오"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시된 데이터 (29일치)
    cached_data = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 100, "middle": 10000.0, "upper": 11000.0, "lower": 9000.0}
        for i in range(29)
    ]
    mock_cache.get_raw.return_value = ({"data": cached_data}, None)
    
    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    assert isinstance(result.data[0], ResBollingerBand)

@pytest.mark.asyncio
async def test_get_rsi_caching_hit(indicator_service_with_cache):
    """RSI: 캐시 히트 시나리오"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시된 데이터 (29일치 시계열)
    cached_series = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 100, "rsi": 50.0}
        for i in range(29)
    ]
    mock_cache.get_raw.return_value = ({"data": cached_series}, None)
    
    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data, ResRSI)
    assert result.data.date == "20250130"

@pytest.mark.asyncio
async def test_calculate_indicators_full_empty_data(indicator_service):
    """_calculate_indicators_full: 빈 데이터 처리"""
    service, _ = indicator_service
    result = service._calculate_indicators_full("005930", [])
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value

@pytest.mark.asyncio
async def test_calculate_indicators_full_exception(indicator_service):
    """_calculate_indicators_full: 예외 처리"""
    service, _ = indicator_service
    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Test Error")):
        result = service._calculate_indicators_full("005930", [{"date": "20250101"}])
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value

@pytest.mark.asyncio
async def test_get_moving_average_caching_miss(indicator_service_with_cache):
    """MA: 캐시 미스 -> 전체 계산 및 저장"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 30일치 데이터
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시 미스 설정
    mock_cache.get_raw.return_value = None
    
    result = await service.get_moving_average("005930", period=5)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert "ma_005930_5_sma" in args[0] # key check
    assert kwargs.get("save_to_file") is True

@pytest.mark.asyncio
async def test_get_moving_average_caching_hit(indicator_service_with_cache):
    """MA: 캐시 히트 -> 증분 계산 및 병합"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 30일치 데이터 (0~29)
    full_data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=full_data)
    
    # 캐시된 데이터 (29일치)
    cached_data = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 10, "ma": 10000.0}
        for i in range(29)
    ]
    mock_cache.get_raw.return_value = ({"data": cached_data}, None)
    
    result = await service.get_moving_average("005930", period=5)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    # 마지막 데이터는 새로 계산된 값이어야 함
    assert result.data[-1].date == "20250130"
    assert isinstance(result.data[0], ResMovingAverage)

@pytest.mark.asyncio
async def test_get_moving_average_caching_hit_partial_fail_fallback(indicator_service_with_cache):
    """MA: 캐시 히트했으나 증분 계산 실패 -> 전체 재계산 Fallback"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    full_data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=full_data)
    
    cached_data = [{"code": "005930", "date": "20250101", "ma": 10000.0}]
    mock_cache.get_raw.return_value = ({"data": cached_data}, None)
    
    # _calculate_moving_average_full 실패 유도
    with patch.object(service, '_calculate_moving_average_full', return_value=ResCommonResponse(rt_cd="1", msg1="Fail")) as mock_calc:
        result = await service.get_moving_average("005930", period=5)
        
        assert result.rt_cd == ErrorCode.SUCCESS.value
        assert len(result.data) == 30
        # Fallback logic (inline pandas) executed

@pytest.mark.asyncio
async def test_calculate_moving_average_full_exception(indicator_service):
    """_calculate_moving_average_full: 예외 처리"""
    service, _ = indicator_service
    
    # 데이터 변환 실패 유도 (close가 숫자가 아님)
    data = [{"date": "20250101", "close": "invalid"}]
    
    # 직접 호출
    result = service._calculate_moving_average_full("005930", data, 5, "sma")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value

@pytest.mark.asyncio
async def test_get_bollinger_bands_caching_miss(indicator_service_with_cache):
    """볼린저 밴드: 캐시 미스 -> 전체 계산 및 저장"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 30일치 데이터
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시 미스
    mock_cache.get_raw.return_value = None
    
    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert "bb_005930" in args[0]

@pytest.mark.asyncio
async def test_get_bollinger_bands_caching_hit_merge_update(indicator_service_with_cache):
    """볼린저 밴드: 캐시 히트 & 날짜 중복 -> 업데이트 (중복 방지)"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 30일치 데이터 (마지막 날짜 20250130)
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시된 데이터 (30일치, 마지막 날짜 포함)
    cached_data = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 100, "middle": 10000.0, "upper": 11000.0, "lower": 9000.0}
        for i in range(30)
    ]
    # 마지막 데이터의 값을 다르게 설정하여 업데이트 확인
    cached_data[-1]["close"] = 99999 
    
    mock_cache.get_raw.return_value = ({"data": cached_data}, None)
    
    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    # 마지막 데이터가 API에서 가져온 최신 값으로 업데이트되었는지 확인
    # API data[-1].close = 10000 + 29*100 = 12900
    assert result.data[-1].close == 12900.0
    assert result.data[-1].date == "20250130"

@pytest.mark.asyncio
async def test_get_rsi_caching_miss(indicator_service_with_cache):
    """RSI: 캐시 미스 -> 전체 시계열 계산 및 저장"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    mock_cache.get_raw.return_value = None
    
    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert "rsi_series_005930" in args[0]

@pytest.mark.asyncio
async def test_get_relative_strength_past_close_zero_via_api(indicator_service):
    """상대강도: API를 통해 조회한 과거 종가가 0 이하인 경우"""
    service, mock_sqs = indicator_service
    
    # 70일치 데이터
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(70)]
    # 과거 시점(63일 전)의 종가를 0으로 설정
    data[-63]["close"] = 0
    
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    result = await service.get_relative_strength("005930", period_days=63)
    
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "과거 종가가 0 이하" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_caching_hit_merge_update(indicator_service_with_cache):
    """MA: 캐시 히트 & 날짜 중복 -> 업데이트 (중복 방지)"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 30일치 데이터
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 10} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시된 데이터 (30일치, 마지막 날짜 포함)
    cached_data = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 10, "ma": 10000.0}
        for i in range(30)
    ]
    # 캐시의 마지막 데이터 값을 다르게 설정
    cached_data[-1]["ma"] = 99999.0
    
    mock_cache.get_raw.return_value = ({"data": cached_data}, None)
    
    result = await service.get_moving_average("005930", period=5)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert len(result.data) == 30
    # 마지막 데이터가 새로 계산된 값으로 업데이트되었는지 확인 (99999.0이 아니어야 함)
    assert result.data[-1].ma != 99999.0
    assert result.data[-1].date == "20250130"

@pytest.mark.asyncio
async def test_get_rsi_caching_hit_partial_calc_none(indicator_service_with_cache):
    """RSI: 캐시 히트했으나 증분 계산 결과가 None인 경우 (데이터 부족 등)"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 충분한 데이터가 있지만, 증분 계산 시에는 일부만 사용됨
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i * 100} for i in range(30)]
    mock_sqs.get_ohlcv.return_value = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)
    
    # 캐시된 데이터
    cached_series = [
        {"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i * 100, "rsi": 50.0}
        for i in range(29)
    ]
    mock_cache.get_raw.return_value = ({"data": cached_series}, None)
    
    # _calculate_rsi_series가 호출될 때, 마지막 데이터의 RSI가 None이 되도록 조작
    with patch.object(service, '_calculate_rsi_series') as mock_calc:
        # 정상적인 응답 구조지만 rsi가 None
        mock_calc.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, 
            msg1="OK", 
            data=[{"rsi": None}]
        )
        
        result = await service.get_rsi("005930")
        
        assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
        assert "계산 불가" in result.msg1

@pytest.mark.asyncio
async def test_calculate_indicators_full_safe_float_handling(indicator_service):
    """_calculate_indicators_full: NaN/Inf 값 처리 (_safe_float)"""
    service, _ = indicator_service
    
    # 데이터 부족으로 MA 계산 시 NaN 발생 유도
    data = [
        {"date": "20250101", "close": 10000},
        {"date": "20250102", "close": 10000}
    ]
    
    result = service._calculate_indicators_full("005930", data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    indicators = result.data
    
    # MA5는 데이터 부족으로 NaN -> None으로 변환되어야 함
    assert indicators["ma5"][0]["ma"] is None
    assert indicators["ma5"][1]["ma"] is None
    
    # BB도 NaN -> None
    assert indicators["bb"][0]["middle"] is None

@pytest.mark.asyncio
async def test_calculate_bollinger_bands_full_exception(indicator_service):
    """_calculate_bollinger_bands_full: 내부 예외 처리"""
    service, _ = indicator_service
    
    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("BB Calc Error")):
        result = service._calculate_bollinger_bands_full("005930", [], 20, 2.0)
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "BB Calc Error" in result.msg1

@pytest.mark.asyncio
async def test_calculate_rsi_series_exception(indicator_service):
    """_calculate_rsi_series: 내부 예외 처리"""
    service, _ = indicator_service
    
    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("RSI Calc Error")):
        result = service._calculate_rsi_series("005930", [], 14)
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "RSI Calc Error" in result.msg1

@pytest.mark.asyncio
async def test_get_chart_indicators_merge_missing_key(indicator_service_with_cache):
    """get_chart_indicators: 병합 시 최신 데이터에 키가 없는 경우"""
    service, mock_sqs, mock_cache = indicator_service_with_cache
    
    # 140개 데이터
    data = [{"date": f"202501{i+1:03d}", "close": 10000 + i} for i in range(140)]
    
    # 캐시 히트 (extra_key 포함)
    cached_indicators = {
        "ma5": [{"date": "old", "ma": 100}],
        "extra_key": [{"date": "old", "val": 1}] 
    }
    mock_cache.get_raw.return_value = ({"data": cached_indicators}, None)
    
    result = await service.get_chart_indicators("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # extra_key는 업데이트되지 않고 그대로 유지되어야 함 (else 분기)
    assert "extra_key" in result.data
    assert len(result.data["extra_key"]) == 1