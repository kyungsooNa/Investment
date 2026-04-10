import pytest
import json
import numpy as np
from unittest.mock import MagicMock, AsyncMock
from dataclasses import asdict

from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.first_pullback_types import FirstPullbackConfig
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_first_pullback_json_serializable_no_type_error():
    """
    [첫눌림목] 전략에서 가상매매 DB(pandas)로부터 전달된 
    numpy.float64 타입의 데이터를 처리할 때,
    생성된 TradeSignal이 정상적으로 JSON 직렬화되는지(TypeError 예외가 발생하지 않는지) 검증합니다.
    """
    # 1. Mocks 준비
    mock_sqs = MagicMock()
    
    # 현재가를 진입가(10,000원) 대비 +15% 상승한 11,500원으로 설정 (부분 익절 10.15% 조건을 트리거하기 위함)
    mock_sqs.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "11500"}}
    ))
    
    # 20MA 계산을 위한 20일치 더미 데이터 (손절 체크 방어용)
    mock_sqs.get_recent_daily_ohlcv = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 10000, "volume": 1000}] * 20
    ))

    mock_universe = MagicMock()
    mock_tm = MagicMock()

    # 2. 전략 생성 (부분익절 하한선 10% 설정)
    config = FirstPullbackConfig(take_profit_lower_pct=10.0, partial_sell_ratio=0.5)
    strategy = FirstPullbackStrategy(
        stock_query_service=mock_sqs,
        universe_service=mock_universe,
        market_clock=mock_tm,
        config=config,
        logger=MagicMock()
    )

    # 3. Pandas DataFrame에서 온 것처럼 numpy 자료형으로 holding 데이터 구성
    holdings = [{
        "code": "001820",
        "name": "테스트종목",
        "buy_price": np.float64(10000.0),  # 🚨 Numpy Float 타입 의도적 주입
        "qty": np.float64(4.0)             # 🚨 수량도 Numpy Float 타입 주입
    }]

    # 4. 출구 조건(check_exits) 검사 실행
    signals = await strategy.check_exits(holdings)

    # 익절 시그널이 1개 발생해야 함
    assert len(signals) == 1
    signal = signals[0]
    
    # 5. JSON 직렬화 테스트 (핵심 검증)
    try:
        serialized = json.dumps(asdict(signal))
        assert "부분익절" in serialized
    except TypeError as e:
        pytest.fail(f"JSON 직렬화 에러 발생: numpy 타입 캐스팅 누락이 의심됩니다. {e}")
        
    # 6. 내부 속성 타입 단언 (순수 int, float 확인)
    assert type(signal.price).__name__ != 'float64'
    assert type(signal.qty).__name__ != 'float64'
    assert isinstance(signal.price, (int, float))
    assert isinstance(signal.qty, int)