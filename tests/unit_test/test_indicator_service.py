import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import math
from services.indicator_service import IndicatorService
from common.types import ResCommonResponse, ErrorCode, ResBollingerBand, ResRSI, ResMovingAverage, ResRelativeStrength

@pytest.fixture
def indicator_service():
    mock_trading_service = AsyncMock()
    mock_cache_manager = MagicMock()
    return IndicatorService(mock_trading_service, cache_manager=mock_cache_manager), mock_trading_service

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


# ═══════════════════════════════════════════════════════
# RS (상대강도) 테스트
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_relative_strength_success(indicator_service):
    """3개월 수익률 정상 계산"""
    service, mock_ts = indicator_service

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
    service, mock_ts = indicator_service

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
    service, mock_ts = indicator_service

    data = [{"date": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
             "close": 10000, "open": 10000, "high": 10000, "low": 10000, "volume": 100}
            for i in range(70)]

    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data.return_pct == 0.0  # 종가 일정 → 수익률 0%
    mock_ts.get_ohlcv.assert_not_called()


@pytest.mark.asyncio
async def test_get_relative_strength_zero_past_close(indicator_service):
    """과거 종가가 0이면 EMPTY_VALUES 반환"""
    service, mock_ts = indicator_service

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
    service, mock_ts = indicator_service
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="Fail", data=None
    )
    
    data, err = await service._get_ohlcv_data("005930", "D")
    assert data is None
    assert err.rt_cd == ErrorCode.API_ERROR.value

@pytest.mark.asyncio
async def test_get_bollinger_bands_string_conversion_success(indicator_service):
    """볼린저 밴드: 문자열로 된 숫자 데이터가 정상적으로 변환되어 계산되는지 테스트"""
    service, mock_ts = indicator_service
    
    # 문자열 데이터
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 100)} for i in range(25)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_bollinger_bands("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data[-1].close == 12400.0 # float로 변환됨 확인

@pytest.mark.asyncio
async def test_get_rsi_string_conversion_success(indicator_service):
    """RSI: 문자열 데이터 변환 성공 테스트"""
    service, mock_ts = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 100)} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data.rsi, float)

@pytest.mark.asyncio
async def test_get_rsi_nan_result(indicator_service):
    """RSI: 계산 결과가 NaN인 경우 (데이터 값 문제)"""
    service, mock_ts = indicator_service
    
    # 데이터 개수는 충분하지만 값이 모두 None인 경우 -> 결과 NaN
    data_all_nan = [{"date": f"202501{i+1:02d}", "close": None} for i in range(15)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data_all_nan
    )
    
    result = await service.get_rsi("005930", period=14)
    
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "계산 불가" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_unknown_method(indicator_service):
    """이동평균: 알 수 없는 method는 SMA로 처리"""
    service, mock_ts = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(20)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930", method="unknown_method")
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_relative_strength_generic_exception(indicator_service):
    """상대강도: 계산 중 알 수 없는 예외 발생 시 처리"""
    service, mock_ts = indicator_service
    
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
    service, mock_ts = indicator_service
    
    # 데이터는 정상이지만 내부 로직에서 예외 발생 유도
    data = [{"date": "20250101", "close": 10000}] * 25
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_bollinger_bands("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "볼린저 밴드 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_unknown_exception(indicator_service):
    """RSI: 알 수 없는 예외 발생 시 UNKNOWN_ERROR 반환"""
    service, mock_ts = indicator_service
    
    data = [{"date": "20250101", "close": 10000}] * 30
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_rsi("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "RSI 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_unknown_exception(indicator_service):
    """이동평균: 알 수 없는 예외 발생 시 UNKNOWN_ERROR 반환"""
    service, mock_ts = indicator_service
    
    data = [{"date": "20250101", "close": 10000}] * 25
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    with patch("services.indicator_service.pd.DataFrame", side_effect=Exception("Unexpected Error")):
        result = await service.get_moving_average("005930")
        
        assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
        assert "MA 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_relative_strength_string_conversion(indicator_service):
    """상대강도: 문자열 데이터 변환 성공 테스트"""
    service, mock_ts = indicator_service
    
    # 문자열 데이터 (period_days=63 이므로 충분한 데이터 생성)
    data = [{"date": f"202501{i+1:02d}", "close": str(10000 + i * 10)} for i in range(70)]
    
    result = await service.get_relative_strength("005930", period_days=63, ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert isinstance(result.data.return_pct, float)

@pytest.mark.asyncio
async def test_get_ohlcv_data_success_but_no_data(indicator_service):
    """_get_ohlcv_data: API 성공이지만 데이터가 없는(빈 리스트) 경우"""
    service, mock_ts = indicator_service
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_ts = indicator_service
    
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=[]
    )

    result = await service.get_rsi("005930")

    # resp를 그대로 반환하므로 SUCCESS, data=[] 이어야 함
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == []

@pytest.mark.asyncio
async def test_get_bollinger_bands_missing_column(indicator_service):
    """볼린저 밴드: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_ts = indicator_service
    
    # 'close' 키가 없는 데이터
    data = [{"date": "20250101", "open": 10000}] * 25
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_ts = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 30
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "RSI 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_moving_average_missing_column(indicator_service):
    """이동평균: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_ts = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 25
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_moving_average("005930")
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "MA 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_relative_strength_missing_column(indicator_service):
    """상대강도: 데이터에 'close' 컬럼이 없는 경우 (KeyError -> UNKNOWN_ERROR)"""
    service, mock_ts = indicator_service
    
    data = [{"date": "20250101", "open": 10000}] * 70
    
    result = await service.get_relative_strength("005930", ohlcv_data=data)
    
    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "상대강도 계산 중 오류" in result.msg1

@pytest.mark.asyncio
async def test_get_rsi_constant_price(indicator_service):
    """RSI: 가격이 일정할 때 (변동폭 0 -> RSI NaN) 처리 검증"""
    service, mock_ts = indicator_service
    
    # 가격이 10000원으로 일정
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    result = await service.get_rsi("005930")

    # RSI가 NaN이므로 EMPTY_VALUES 반환 예상
    assert result.rt_cd == ErrorCode.EMPTY_VALUES.value
    assert "계산 불가" in result.msg1

@pytest.mark.asyncio
async def test_get_bollinger_bands_constant_price(indicator_service):
    """볼린저 밴드: 가격이 일정할 때 (표준편차 0) 검증"""
    service, mock_ts = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(25)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
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
    service, mock_ts = indicator_service
    
    data = [{"date": f"202501{i+1:02d}", "close": 10000} for i in range(10)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )

    # "EMA" 대문자로 호출
    result = await service.get_moving_average("005930", period=5, method="EMA")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    # EMA 로직을 탔다면 결과가 나와야 함
    assert result.data[-1].ma == 10000.0

@pytest.mark.asyncio
async def test_get_chart_indicators_caching(indicator_service):
    """get_chart_indicators 캐싱 동작 테스트"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 150일치 데이터 (캐싱 조건 충족: len >= 130)
    data = [{"date": f"202501{i+1:03d}", "close": 10000 + i} for i in range(150)]
    
    # 1. 캐시 미스 (첫 호출)
    mock_cache.get_raw.return_value = (None, None)
    
    result1 = await service.get_chart_indicators("005930", data)
    
    assert result1.rt_cd == ErrorCode.SUCCESS.value
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    # 키에 종목코드와 날짜가 포함되어야 함
    assert "indicators_chart_005930_" in args[0]
    assert kwargs['save_to_file'] is True
    
    # 2. 캐시 히트 (두 번째 호출)
    # 저장된 캐시 데이터(과거 데이터)를 반환하도록 설정
    # 실제 로직: past_indicators는 data[:-1]에 대한 결과
    past_indicators = result1.data.copy()
    # 마지막 요소 제거 (캐시된 상태 시뮬레이션)
    for k in past_indicators:
        if isinstance(past_indicators[k], list):
            past_indicators[k] = past_indicators[k][:-1]
            
    mock_cache.get_raw.return_value = ({"data": past_indicators}, "memory")
    
    result2 = await service.get_chart_indicators("005930", data)
    
    assert result2.rt_cd == ErrorCode.SUCCESS.value
    # 캐시 히트 시 set은 다시 호출되지 않아야 함 (이전 호출 1회 유지)
    assert mock_cache.set.call_count == 1
    # 결과 데이터 길이는 원본 데이터 길이와 같아야 함 (병합됨)
    assert len(result2.data['ma5']) == 150

@pytest.mark.asyncio
async def test_get_chart_indicators_cache_miss_none_return(indicator_service):
    """get_chart_indicators: 캐시 매니저가 None을 반환할 때(완전한 캐시 미스) 처리 검증"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 데이터 준비 (캐싱 조건 충족: len >= 130)
    data = [{"date": f"202501{i+1:03d}", "close": 10000 + i} for i in range(150)]
    
    # 캐시 미스 시 None 반환 설정 (이전 코드에서 언패킹 에러 발생했던 상황)
    mock_cache.get_raw.return_value = None
    
    # 실행
    result = await service.get_chart_indicators("005930", data)
    
    # 검증
    assert result.rt_cd == ErrorCode.SUCCESS.value
    # 캐시 미스 처리되어 전체 계산 후 저장되었는지 확인
    mock_cache.set.assert_called()
    assert len(result.data['ma5']) == 150

def test_compute_ma_logic():
    """_compute_ma 정적 메서드의 SMA/EMA 계산 로직 검증"""
    # Arrange
    data = {"close": [10.0, 20.0, 30.0, 40.0, 50.0]}
    df = pd.DataFrame(data)
    
    # Act 1: SMA (Simple Moving Average)
    # Period 3: (10+20+30)/3 = 20, (20+30+40)/3 = 30, ...
    df_sma = IndicatorService._compute_ma(df.copy(), period=3, method="sma", target_col="sma_3")
    
    # Assert SMA
    # 처음 period-1 개는 NaN이어야 함
    assert pd.isna(df_sma["sma_3"].iloc[0])
    assert pd.isna(df_sma["sma_3"].iloc[1])
    # 이후 값 검증
    assert df_sma["sma_3"].iloc[2] == 20.0
    assert df_sma["sma_3"].iloc[3] == 30.0
    assert df_sma["sma_3"].iloc[4] == 40.0
    
    # Act 2: EMA (Exponential Moving Average)
    # Period 3 (span=3) -> alpha = 2/(3+1) = 0.5 (pandas ewm adjust=False 기준)
    # y0 = 10
    # y1 = 0.5*20 + 0.5*10 = 15
    # y2 = 0.5*30 + 0.5*15 = 22.5
    # y3 = 0.5*40 + 0.5*22.5 = 31.25
    # y4 = 0.5*50 + 0.5*31.25 = 40.625
    df_ema = IndicatorService._compute_ma(df.copy(), period=3, method="ema", target_col="ema_3")
    
    # Assert EMA
    assert df_ema["ema_3"].iloc[0] == 10.0
    assert df_ema["ema_3"].iloc[1] == 15.0
    assert df_ema["ema_3"].iloc[2] == 22.5
    assert df_ema["ema_3"].iloc[3] == 31.25
    assert df_ema["ema_3"].iloc[4] == 40.625

@pytest.mark.asyncio
async def test_cache_key_changes_on_parameter_change(indicator_service):
    """지표 파라미터 변경 시 캐시 키 변경 확인 테스트"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 데이터 준비 (캐싱 조건 충족을 위해 충분한 데이터)
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )
    
    # 1. MA Period 변경 테스트
    # Period 5
    mock_cache.get_raw.return_value = None # Cache Miss
    await service.get_moving_average("005930", period=5)
    
    # 첫 번째 호출의 캐시 키 확인
    args_5, _ = mock_cache.get_raw.call_args
    key_5 = args_5[0]
    assert "ma_005930_5_sma" in key_5
    
    # Period 10
    mock_cache.get_raw.reset_mock()
    await service.get_moving_average("005930", period=10)
    
    args_10, _ = mock_cache.get_raw.call_args
    key_10 = args_10[0]
    assert "ma_005930_10_sma" in key_10
    
    assert key_5 != key_10
    
    # 2. BB Std Dev 변경 테스트
    mock_cache.get_raw.reset_mock()
    
    # Std Dev 2.0
    await service.get_bollinger_bands("005930", period=20, std_dev=2.0)
    args_20, _ = mock_cache.get_raw.call_args
    key_20 = args_20[0]
    assert "bb_005930_20_2.0" in key_20
    
    # Std Dev 2.5
    mock_cache.get_raw.reset_mock()
    await service.get_bollinger_bands("005930", period=20, std_dev=2.5)
    args_25, _ = mock_cache.get_raw.call_args
    key_25 = args_25[0]
    assert "bb_005930_20_2.5" in key_25
    
    assert key_20 != key_25

def test_compute_ma_logic():
    """_compute_ma 정적 메서드의 SMA/EMA 계산 로직 검증"""
    data = {"close": [10.0, 20.0, 30.0, 40.0, 50.0]}
    df = pd.DataFrame(data)
    
    # SMA
    df_sma = IndicatorService._compute_ma(df.copy(), period=3, method="sma", target_col="sma_3")
    assert pd.isna(df_sma["sma_3"].iloc[1])
    assert df_sma["sma_3"].iloc[2] == 20.0
    
    # EMA
    df_ema = IndicatorService._compute_ma(df.copy(), period=3, method="ema", target_col="ema_3")
    assert df_ema["ema_3"].iloc[0] == 10.0
    assert df_ema["ema_3"].iloc[1] == 15.0

def test_compute_bb_logic():
    """_compute_bb 정적 메서드 로직 검증"""
    data = {"close": [100, 110, 120, 130, 140]}
    df = pd.DataFrame(data)
    
    df_bb = IndicatorService._compute_bb(df.copy(), period=3, std_dev=2.0, prefix="bb")
    
    assert df_bb["bb_middle"].iloc[2] == 110.0
    assert df_bb["bb_upper"].iloc[2] == 130.0
    assert df_bb["bb_lower"].iloc[2] == 90.0

def test_compute_rsi_logic():
    """_compute_rsi 정적 메서드 로직 검증"""
    data = {"close": [100, 110, 120, 130, 140, 150]}
    df = pd.DataFrame(data)
    
    df_rsi = IndicatorService._compute_rsi(df.copy(), period=2, target_col="rsi")
    
    assert "rsi" in df_rsi.columns
    assert df_rsi["rsi"].iloc[-1] > 90.0

@pytest.mark.asyncio
async def test_cache_key_changes_on_data_update(indicator_service):
    """데이터의 마지막 날짜가 변경되었을 때 캐시 키가 변경되는지 확인"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 데이터 1: 20250101 ~ 20250129 (29일치) -> confirmed: ~20250128
    data1 = [{"date": f"202501{i+1:02d}", "close": 10000 + i} for i in range(29)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data1
    )
    
    mock_cache.get_raw.return_value = None # Cache Miss
    await service.get_moving_average("005930", period=5)
    
    args1, _ = mock_cache.get_raw.call_args
    key1 = args1[0]
    # key format: ma_{code}_{period}_{method}_{date}
    # confirmed last date is 20250128
    assert "20250128" in key1
    
    # 데이터 2: 20250101 ~ 20250130 (30일치) -> confirmed: ~20250129
    data2 = [{"date": f"202501{i+1:02d}", "close": 10000 + i} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data2
    )
    
    mock_cache.get_raw.reset_mock()
    await service.get_moving_average("005930", period=5)
    
    args2, _ = mock_cache.get_raw.call_args
    key2 = args2[0]
    assert "20250129" in key2
    
    assert key1 != key2

def test_compute_bb_logic():
    """_compute_bb 정적 메서드 로직 검증"""
    data = {"close": [100, 110, 120, 130, 140]}
    df = pd.DataFrame(data)
    
    # Period 3, Std 2.0
    # 0: NaN
    # 1: NaN
    # 2: Mean(100,110,120)=110, Std(100,110,120)=10.0
    #    Upper = 110 + 2*10 = 130
    #    Lower = 110 - 2*10 = 90
    df_bb = IndicatorService._compute_bb(df.copy(), period=3, std_dev=2.0, prefix="bb")
    
    assert "bb_middle" in df_bb.columns
    assert "bb_upper" in df_bb.columns
    assert "bb_lower" in df_bb.columns
    
    assert pd.isna(df_bb["bb_middle"].iloc[1])
    assert df_bb["bb_middle"].iloc[2] == 110.0
    assert df_bb["bb_upper"].iloc[2] == 130.0
    assert df_bb["bb_lower"].iloc[2] == 90.0

def test_compute_rsi_logic():
    """_compute_rsi 정적 메서드 로직 검증"""
    # 상승 추세 데이터
    data = {"close": [100, 110, 120, 130, 140, 150]}
    df = pd.DataFrame(data)
    
    # Period 2
    df_rsi = IndicatorService._compute_rsi(df.copy(), period=2, target_col="rsi")
    
    assert "rsi" in df_rsi.columns
    # 지속 상승이므로 RSI는 100에 가까워야 함
    assert df_rsi["rsi"].iloc[-1] > 90.0

@pytest.mark.asyncio
async def test_caching_behavior_hit_and_miss(indicator_service):
    """캐시 히트/미스 동작 검증"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 데이터 준비 (30일치)
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )
    
    # 1. Cache Miss
    mock_cache.get_raw.return_value = None
    
    await service.get_moving_average("005930", period=5)
    
    # 캐시 저장 호출 확인
    mock_cache.set.assert_called_once()
    
    # 2. Cache Hit
    # 저장된 것과 유사한 구조의 데이터 반환 설정
    # confirmed_data는 data[:-1] (29개)
    cached_data = [{"date": f"202501{i+1:02d}", "close": 10000 + i, "ma": 10000} for i in range(29)]
    cached_data = [{"code": "005930", "date": f"202501{i+1:02d}", "close": 10000 + i, "ma": 10000} for i in range(29)]
    mock_cache.get_raw.return_value = ({"data": cached_data}, "memory")
    
    mock_cache.set.reset_mock()
    
    result = await service.get_moving_average("005930", period=5)
    
    # 캐시 히트 시 set은 호출되지 않아야 함 (증분 계산만 수행하고 리턴)
    mock_cache.set.assert_not_called()
    
    # 결과는 30개여야 함 (캐시된 29개 + 오늘 1개)
    assert len(result.data) == 30
    assert result.data[-1].date == "20250130"

@pytest.mark.asyncio
async def test_cache_key_changes_on_parameter_change(indicator_service):
    """지표 파라미터 변경 시 캐시 키 변경 확인 테스트"""
    service, mock_ts = indicator_service
    mock_cache = service.cache_manager
    
    # 데이터 준비 (캐싱 조건 충족을 위해 충분한 데이터)
    data = [{"date": f"202501{i+1:02d}", "close": 10000 + i} for i in range(30)]
    mock_ts.get_ohlcv.return_value = ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data
    )
    
    # 1. MA Period 변경 테스트
    # Period 5
    mock_cache.get_raw.return_value = None # Cache Miss
    await service.get_moving_average("005930", period=5)
    
    # 첫 번째 호출의 캐시 키 확인
    args_5, _ = mock_cache.get_raw.call_args
    key_5 = args_5[0]
    assert "ma_005930_5_sma" in key_5
    
    # Period 10
    mock_cache.get_raw.reset_mock()
    await service.get_moving_average("005930", period=10)
    
    args_10, _ = mock_cache.get_raw.call_args
    key_10 = args_10[0]
    assert "ma_005930_10_sma" in key_10
    
    assert key_5 != key_10
    
    # 2. BB Std Dev 변경 테스트
    mock_cache.get_raw.reset_mock()
    
    # Std Dev 2.0
    await service.get_bollinger_bands("005930", period=20, std_dev=2.0)
    args_20, _ = mock_cache.get_raw.call_args
    key_20 = args_20[0]
    assert "bb_005930_20_2.0" in key_20
    
    # Std Dev 2.5
    mock_cache.get_raw.reset_mock()
    await service.get_bollinger_bands("005930", period=20, std_dev=2.5)
    args_25, _ = mock_cache.get_raw.call_args
    key_25 = args_25[0]
    assert "bb_005930_20_2.5" in key_25
    
    assert key_20 != key_25