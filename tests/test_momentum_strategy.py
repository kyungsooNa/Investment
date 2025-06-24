import pytest
from unittest.mock import AsyncMock
from services.momentum_strategy import MomentumStrategy

@pytest.mark.asyncio
async def test_momentum_strategy_live_mode():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value={
        "symbol": "005930",
        "open": 10000,
        "current": 11000,
        "change_rate": 10.0
    })
    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_prpr": "11500"
        }
    })
    mock_quotations.get_stock_name_by_code = AsyncMock(return_value="삼성전자")  # ✅ 이거 추가

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live"
    )

    result = await strategy.run(["005930"])

    assert result["follow_through"] == [{
        "code": "005930",
        "name": "삼성전자"
    }]
    assert result["not_follow_through"] == []


@pytest.mark.asyncio
async def test_momentum_strategy_live_mode_not_follow():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value={
        "symbol": "000660",
        "open": 10000,
        "current": 11000,
        "change_rate": 10.0
    })

    mock_quotations.get_current_price = AsyncMock(return_value={
        "output": {
            "stck_prpr": "11200"  # 1.8% 상승 → Follow Through 조건 미달
        }
    })

    mock_quotations.get_stock_name_by_code = AsyncMock(return_value="SK하이닉스")

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live"
    )

    result = await strategy.run(["000660"])

    assert result["follow_through"] == []
    assert result["not_follow_through"] == [{
        "code": "000660",
        "name": "SK하이닉스"
    }]


@pytest.mark.asyncio
async def test_momentum_strategy_backtest_mode():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value={
        "symbol": "035720",
        "open": 300000,
        "current": 330000,
        "change_rate": 10.0
    })
    mock_quotations.get_stock_name_by_code = AsyncMock(return_value="카카오")

    async def dummy_backtest_lookup(code):
        return 350000  # 6.06% 추가 상승

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=5.0,
        min_follow_through_time=10,
        mode="backtest",
        backtest_lookup=dummy_backtest_lookup
    )

    result = await strategy.run(["035720"])

    assert result["follow_through"] == [{"code": "035720", "name": "카카오"}]
    assert result["not_follow_through"] == []


@pytest.mark.asyncio
async def test_momentum_strategy_backtest_no_lookup_raises():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value={
        "symbol": "035420",
        "open": 70000,
        "current": 77000,
        "change_rate": 10.0
    })

    strategy = MomentumStrategy(
        quotations=mock_quotations,
        mode="backtest",  # but no backtest_lookup provided
    )

    with pytest.raises(ValueError):
        await strategy.run(["035420"])
