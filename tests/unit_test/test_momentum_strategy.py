import pytest
from unittest.mock import AsyncMock
from strategies.momentum_strategy import MomentumStrategy
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_momentum_strategy_live_mode():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "symbol": "005930",
            "open": 10000,
            "current": 11000,
            "change_rate": 10.0
        }
    ))
    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "stck_prpr": "11500"
        }
    ))
    mock_quotations.get_name_by_code = AsyncMock(return_value="삼성전자")

    strategy = MomentumStrategy(
        broker=mock_quotations,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live"
    )

    result = await strategy.run(["005930"])

    assert isinstance(result, dict)
    assert "follow_through" in result
    assert "not_follow_through" in result
    assert result["follow_through"][0]["code"] == "005930"

@pytest.mark.asyncio
async def test_momentum_strategy_live_mode_not_follow():
    mock_quotations = AsyncMock()
    mock_quotations.get_price_summary = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
        "symbol": "000660",
        "open": 10000,
        "current": 11000,
        "change_rate": 10.0
        }
    ))

    mock_quotations.get_current_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "stck_prpr": "11200"  # 1.8% 상승
        }
    ))

    mock_quotations.get_name_by_code = AsyncMock(return_value="SK하이닉스")

    strategy = MomentumStrategy(
        broker=mock_quotations,
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

    mock_quotations.get_price_summary = AsyncMock(return_value=ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "symbol": "035720",
            "open": 300000,
            "current": 330000,
            "change_rate": 10.0
        }
    ))
    mock_quotations.get_name_by_code = AsyncMock(return_value="카카오")  # ✅ 핵심 수정

    async def dummy_backtest_lookup(code, summary, minutes_after):
        return 350000  # 6.06% 추가 상승을 의미하는 가짜 가격

    strategy = MomentumStrategy(
        broker=mock_quotations,
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
        broker=mock_quotations,
        mode="backtest",  # but no backtest_lookup provided
    )

    with pytest.raises(ValueError):
        await strategy.run(["035420"])
