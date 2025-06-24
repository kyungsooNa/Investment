import pytest
from unittest.mock import AsyncMock

from services.strategy_executor import StrategyExecutor
from services.momentum_strategy import MomentumStrategy

@pytest.mark.asyncio
async def test_strategy_executor_with_mocked_quotations():
    mock_quotations = AsyncMock()

    # get_price_summary는 리스트 순서대로 결과 반환
    mock_quotations.get_price_summary.side_effect = [
        {"symbol": "0001", "open": 10000, "current": 11000, "change_rate": 10.0},
        {"symbol": "0002", "open": 20000, "current": 24000, "change_rate": 20.0},
        {"symbol": "0003", "open": 15000, "current": 16000, "change_rate": 6.7}
    ]

    # get_current_price는 async 함수이므로 async mock 함수로 구현
    async def mock_get_current_price(code):
        data_map = {
            "0001": {"output": {"stck_prpr": "11500"}},
            "0002": {"output": {"stck_prpr": "25000"}},
            "0003": {"output": {"stck_prpr": "16500"}},
        }
        return data_map.get(code, {"output": {"stck_prpr": "0"}})

    mock_quotations.get_current_price.side_effect = mock_get_current_price

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live"
    )

    executor = StrategyExecutor(strategy=strategy)

    result = await executor.execute(["0001", "0002", "0003"])

    assert "follow_through" in result
    assert "not_follow_through" in result

    assert result["follow_through"] == ["0001", "0002"]
    assert result["not_follow_through"] == ["0003"]

@pytest.mark.asyncio
async def test_strategy_executor_in_backtest_mode():
    # 1. Mock Quotations 객체
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary.side_effect = [
        {"symbol": "005930", "open": 70000, "current": 77000, "change_rate": 10.0},
        {"symbol": "000660", "open": 100000, "current": 105000, "change_rate": 5.0}
    ]

    # 2. Mock backtest price lookup
    async def mock_backtest_lookup(code):
        return {
            "005930": 80000,   # 상승률 3.9%
            "000660": 106000   # 상승률 0.95%
        }[code]

    # 3. 전략 생성 (backtest 모드)
    strategy = MomentumStrategy(
        quotations=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,  # 10분 후 상승률 기준으로 판단
        mode="backtest",
        backtest_lookup=mock_backtest_lookup
    )

    executor = StrategyExecutor(strategy=strategy)

    # 4. 실행
    result = await executor.execute(["005930", "000660"])

    # 5. 검증
    assert result["follow_through"] == ["005930"]
    assert result["not_follow_through"] == ["000660"]

import pytest
from unittest.mock import AsyncMock
from services.strategy_executor import StrategyExecutor
from services.momentum_strategy import MomentumStrategy


@pytest.mark.asyncio
async def test_strategy_executor_backtest_mode_without_lookup_raises():
    # Mock Quotations 객체 생성
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary.return_value = {
        "symbol": "005930",
        "open": 70000,
        "current": 77000,
        "change_rate": 10.0
    }

    # backtest_lookup 없이 생성
    strategy = MomentumStrategy(
        quotations=mock_quotations,
        mode="backtest"  # backtest 모드 설정
        # backtest_lookup intentionally omitted
    )

    executor = StrategyExecutor(strategy=strategy)

    # 예외 발생 검증
    with pytest.raises(ValueError, match="Backtest 모드에서는 backtest_lookup 함수가 필요합니다."):
        await executor.execute(["005930"])

@pytest.mark.asyncio
async def test_strategy_executor_live_mode_without_backtest_lookup():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary.return_value = {
        "symbol": "005930",
        "open": 70000,
        "current": 77000,
        "change_rate": 10.0
    }
    # 실제 API 응답 구조에 맞게 dict로 반환
    mock_quotations.get_current_price.return_value = {
        "output": {
            "stck_prpr": "80000"
        }
    }

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        mode="live"
    )

    executor = StrategyExecutor(strategy=strategy)

    result = await executor.execute(["005930"])

    assert "follow_through" in result
    assert "005930" in result["follow_through"]
    assert result["not_follow_through"] == []
