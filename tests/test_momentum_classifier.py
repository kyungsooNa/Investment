# tests/test_strategy_executor.py
import pytest
from unittest.mock import AsyncMock
from services.momentum_strategy import MomentumStrategy
from services.strategy_executor import StrategyExecutor

@pytest.mark.asyncio
async def test_momentum_strategy_classification():
    # Mock quotations 객체 생성
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary.side_effect = [
        {"symbol": "A", "open": 10000, "current": 11000, "change_rate": 10.0},
        {"symbol": "B", "open": 10000, "current": 10500, "change_rate": 5.0},
        {"symbol": "C", "open": 10000, "current": 12000, "change_rate": 20.0}
    ]
    mock_quotations.backtest_price_lookup.side_effect = [
        11330,  # A: 3% 상승
        10600,  # B: 0.95% 상승
        12480   # C: 4% 상승
    ]

    # 전략 및 실행자 구성
    strategy = MomentumStrategy(mock_quotations, min_change_rate=10.0, min_follow_through=3.0)
    executor = StrategyExecutor(strategy)

    # 실행 및 검증
    result = await executor.execute(["A", "B", "C"])

    assert result["follow_through"] == ["A", "C"]
    assert result["not_follow_through"] == ["B"]
