import pytest
from services.momentum_classifier import MomentumStrategyClassifier


@pytest.mark.asyncio
async def test_classify_momentum_follow_through():
    dummy_data = {
        "A": {"change_rate": 12.0, "after_rate": 3.0},
        "B": {"change_rate": 15.0, "after_rate": 1.5},
        "C": {"change_rate": 8.0, "after_rate": 2.5}
    }

    async def mock_lookup(symbol):
        return dummy_data[symbol]

    classifier = MomentumStrategyClassifier(price_lookup=mock_lookup)
    result = await classifier.classify_momentum_follow_through(
        symbols=["A", "B", "C"],
        min_change_rate=10.0,
        min_follow_through=2.0
    )

    assert result["follow_through"] == ["A"]
    assert result["not_follow_through"] == ["B"]
    assert "C" not in result["follow_through"] and "C" not in result["not_follow_through"]
