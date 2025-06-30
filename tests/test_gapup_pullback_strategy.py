import pytest
import logging
from strategies.GapUpPullback_strategy import GapUpPullbackStrategy

class MockBroker:
    def __init__(self, summary_map, name_map):
        self.summary_map = summary_map
        self.name_map = name_map

    async def get_price_summary(self, code):
        return self.summary_map.get(code, {})

    async def get_name_by_code(self, code):
        return self.name_map.get(code, "")

@pytest.mark.asyncio
async def test_gapup_pullback_strategy_selection():
    logger = logging.getLogger("test")
    logger.setLevel(logging.INFO)

    summary_map = {
        "123456": {
            "prev_close": 10000,
            "open": 10550,
            "low": 10200,
            "current": 10450
        },
        "654321": {
            "prev_close": 10000,
            "open": 10400,
            "low": 10350,
            "current": 10360
        }
    }

    name_map = {
        "123456": "후보종목",
        "654321": "제외종목"
    }

    broker = MockBroker(summary_map, name_map)
    strategy = GapUpPullbackStrategy(
        broker=broker,
        min_gap_rate=5.0,
        max_pullback_rate=2.0,
        rebound_rate=2.0,
        logger=logger
    )

    result = await strategy.run(["123456", "654321"])

    assert len(result["gapup_pullback_selected"]) == 1
    assert result["gapup_pullback_selected"][0]["code"] == "123456"
    assert len(result["gapup_pullback_rejected"]) == 1
    assert result["gapup_pullback_rejected"][0]["code"] == "654321"
