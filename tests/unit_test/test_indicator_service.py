import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from services.indicator_service import IndicatorService
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage, ResRelativeStrength

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