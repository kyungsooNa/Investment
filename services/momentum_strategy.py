# services/momentum_strategy.py
from services.interfaces.strategy import Strategy
from typing import List, Dict

class MomentumStrategy(Strategy):
    def __init__(self, quotations, min_change_rate=10.0, min_follow_through=3.0):
        self.quotations = quotations
        self.min_change_rate = min_change_rate
        self.min_follow_through = min_follow_through

    async def run(self, stock_codes: List[str]) -> Dict:
        results = []

        for code in stock_codes:
            summary = await self.quotations.get_price_summary(code)
            after_price = await self.quotations.backtest_price_lookup(code)
            summary["after"] = after_price
            summary["after_rate"] = (
                (after_price - summary["current"]) / summary["current"] * 100
                if summary["current"] else 0
            )
            results.append(summary)

        follow_through = []
        not_follow_through = []
        for s in results:
            if s["change_rate"] >= self.min_change_rate and s["after_rate"] >= self.min_follow_through:
                follow_through.append(s["symbol"])
            else:
                not_follow_through.append(s["symbol"])

        return {
            "follow_through": follow_through,
            "not_follow_through": not_follow_through
        }
