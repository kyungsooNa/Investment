# tests/unit_test/strategies/test_traditional_volume_breakout_strategy.py
import pytest
from unittest.mock import MagicMock, AsyncMock

from strategies.traditional_volume_breakout_strategy import (
    TraditionalVolumeBreakoutStrategy,
    TraditionalVBConfig,
    WatchlistItem,
    PositionState,
)
from common.types import ResCommonResponse, ErrorCode

# pytest fixture to create a default strategy instance with mocked dependencies
@pytest.fixture
def strategy_instance():
    """TraditionalVolumeBreakoutStrategy의 테스트용 인스턴스를 생성합니다."""
    trading_service = MagicMock()
    stock_query_service = MagicMock()
    stock_code_mapper = MagicMock()
    time_manager = MagicMock()
    logger = MagicMock()

    # Mock TimeManager methods
    time_manager.get_current_kst_time.return_value.strftime.return_value = "20230101"
    time_manager.get_market_progress_ratio.return_value = 0.5 # Mock market progress

    strategy = TraditionalVolumeBreakoutStrategy(
        trading_service=trading_service,
        stock_query_service=stock_query_service,
        stock_code_mapper=stock_code_mapper,
        time_manager=time_manager,
        config=TraditionalVBConfig(),
        logger=logger,
    )
    return strategy

@pytest.mark.asyncio
async def test_scan_skips_already_held_stock(strategy_instance: TraditionalVolumeBreakoutStrategy):
    """
    scan() 메서드가 이미 보유 중인 종목(_position_state에 있는 종목)에 대해
    매수 신호를 생성하지 않는지 검증합니다.
    """
    # 1. Setup
    strategy = strategy_instance
    test_code = "005930"
    test_name = "삼성전자"

    # 워치리스트에 매수 조건이 충족될 종목 추가
    strategy._watchlist = {
        test_code: WatchlistItem(
            code=test_code,
            name=test_name,
            high_20d=50000,
            ma_20d=48000,
            avg_vol_20d=1000000,
            avg_trading_value_5d=100_000_000_000,
        )
    }
    strategy._watchlist_date = "20230101" # 워치리스트 빌드 스킵

    # 이미 해당 종목을 보유 중인 것으로 상태 설정
    strategy._position_state = {
        test_code: PositionState(
            breakout_level=50000,
            peak_price=51000,
        )
    }

    # API 응답 모의 설정 (이 테스트에서는 호출되면 안 됨)
    strategy._sqs.handle_get_current_stock_price = AsyncMock()

    # 2. Execute
    signals = await strategy.scan()

    # 3. Assert
    # 매수 신호가 생성되지 않아야 함
    assert len(signals) == 0

    # 보유 종목을 건너뛰었으므로, 가격 조회를 위한 API가 호출되지 않아야 함
    strategy._sqs.handle_get_current_stock_price.assert_not_called()

    # 건너뛰었다는 로그가 기록되었는지 확인
    strategy._logger.debug.assert_called_with({
        "event": "scan_skipped_already_holding",
        "code": test_code,
        "name": test_name,
    })

@pytest.mark.asyncio
async def test_scan_generates_signal_for_new_stock(strategy_instance: TraditionalVolumeBreakoutStrategy):
    """
    scan() 메서드가 보유하지 않은 새 종목에 대해 정상적으로 매수 신호를 생성하는지 검증합니다.
    (기존 로직 회귀 테스트)
    """
    # 1. Setup
    strategy = strategy_instance
    test_code = "005930"
    test_name = "삼성전자"

    # 워치리스트 설정
    strategy._watchlist = {
        test_code: WatchlistItem(
            code=test_code, name=test_name, high_20d=50000, ma_20d=48000,
            avg_vol_20d=1000000, avg_trading_value_5d=100_000_000_000,
        )
    }
    strategy._watchlist_date = "20230101"
    strategy._position_state = {} # 보유 상태는 비어있음

    # API 응답 모의 설정 (매수 조건 충족)
    strategy._sqs.handle_get_current_stock_price = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="성공",
            data={"price": "51000", "acml_vol": "1000000", "name": test_name}
        )
    )

    # 2. Execute
    signals = await strategy.scan()

    # 3. Assert
    assert len(signals) == 1
    signal = signals[0]
    assert signal.code == test_code and signal.action == "BUY" and signal.price == 51000
    strategy._sqs.handle_get_current_stock_price.assert_called_once_with(test_code)
    assert test_code in strategy._position_state
    assert strategy._position_state[test_code].peak_price == 51000