import pytest
import json
import numpy as np
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy
from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from common.types import ResCommonResponse


STRATEGY_CLASSES = [
    FirstPullbackStrategy,
    ProgramBuyFollowStrategy,
    VolumeBreakoutLiveStrategy,
    TraditionalVolumeBreakoutStrategy,
    OneilSqueezeBreakoutStrategy,
    HighTightFlagStrategy,
    OneilPocketPivotStrategy
]


@pytest.mark.asyncio
@pytest.mark.parametrize("StrategyClass", STRATEGY_CLASSES)
async def test_all_strategies_json_serializable_no_type_error(StrategyClass):
    """
    모든 전략에서 가상매매 DB(pandas)로부터 전달된 numpy.float64 타입의 
    데이터를 처리할 때, 생성된 TradeSignal이 정상적으로 JSON 직렬화되는지
    (TypeError 예외가 발생하지 않는지) 공통 검증합니다.
    """
    # 1. Mocks 준비
    mock_sqs = MagicMock()
    
    # 현재가를 8,000원으로 설정 (진입가 10,000원 대비 -20% 손절 트리거)
    mock_sqs.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data={"output": {"stck_prpr": "8000", "stck_hgpr": "8000", "pgtr_ntby_qty": "10000"}}
    ))
    mock_sqs.handle_get_current_stock_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data={"price": "8000", "high": "8000"}
    ))
    
    # 20MA 계산 등을 위한 60일치 더미 데이터 (손절 체크 방어용)
    mock_sqs.get_recent_daily_ohlcv = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0", msg1="OK", data=[{"close": 10000, "volume": 1000}] * 60
    ))

    mock_universe = MagicMock()
    # 마켓 타이밍 악화로 설정 -> OneilPocketPivot 하드스탑 트리거 유도
    mock_universe.is_market_timing_ok = AsyncMock(return_value=False)

    mock_tm = MagicMock()
    # 14:50 이후 시각 설정 → FirstPullbackStrategy MA grace period 즉시 손절 트리거
    mock_tm.get_current_kst_time.return_value = datetime(2025, 1, 1, 15, 0, 0)
    mock_tm.get_market_open_time.return_value = datetime(2025, 1, 1, 9, 0, 0)
    mock_tm.get_market_close_time.return_value = datetime(2025, 1, 1, 15, 30, 0)
    mock_mapper = MagicMock()
    mock_mapper.get_name_by_code = MagicMock(return_value="테스트종목")

    # 2. 전략 생성 (각 전략별 필수 의존성 분기 주입)
    kwargs = {
        "stock_query_service": mock_sqs,
        "market_clock": mock_tm,
        "logger": MagicMock()
    }

    if StrategyClass in [FirstPullbackStrategy, OneilSqueezeBreakoutStrategy, HighTightFlagStrategy, OneilPocketPivotStrategy]:
        kwargs["universe_service"] = mock_universe
    if StrategyClass == TraditionalVolumeBreakoutStrategy:
        kwargs["stock_code_repository"] = mock_mapper

    strategy = StrategyClass(**kwargs)

    # 3. Pandas DataFrame에서 온 것처럼 numpy 자료형으로 holding 데이터 구성
    holdings = [{
        "code": "001820",
        "name": "테스트종목",
        "buy_price": np.float64(10000.0),  # 🚨 Numpy Float 타입 의도적 주입
        "qty": np.float64(4.0)             # 🚨 수량도 Numpy Float 타입 주입
    }]

    # 4. 출구 조건(check_exits) 검사 실행
    signals = await strategy.check_exits(holdings)

    # 손절/하드스탑 조건 등에 의해 시그널이 무조건 1개 발생해야 함
    assert len(signals) == 1, f"{StrategyClass.__name__}에서 매도 시그널이 발생하지 않았습니다."
    signal = signals[0]
    
    # 5. JSON 직렬화 테스트 (핵심 검증)
    try:
        serialized = json.dumps(signal.to_dict())
        assert "001820" in serialized
    except TypeError as e:
        pytest.fail(f"{StrategyClass.__name__} JSON 직렬화 에러 발생: numpy 타입 캐스팅 누락이 의심됩니다. {e}")
        
    # 6. 내부 속성 타입 단언 (순수 int, float 확인)
    assert type(signal.price).__name__ != 'float64'
    assert type(signal.qty).__name__ != 'float64'
    assert isinstance(signal.price, (int, float))
    assert isinstance(signal.qty, int)