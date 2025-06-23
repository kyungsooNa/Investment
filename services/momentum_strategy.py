
# services/momentum_strategy.py
from interfaces.strategy import Strategy
from typing import List, Dict, Optional, Callable

class MomentumStrategy(Strategy):
    def __init__(
        self,
        quotations,
        min_change_rate: float = 10.0,
        min_follow_through: float = 3.0,
        mode: str = "live",  # 'live' or 'backtest'
        backtest_lookup: Optional[Callable[[str], int]] = None
    ):
        self.quotations = quotations
        self.min_change_rate = min_change_rate
        self.min_follow_through = min_follow_through
        self.mode = mode
        self.backtest_lookup = backtest_lookup

    async def run(self, stock_codes: List[str]) -> Dict:
        results = []

        for code in stock_codes:
            summary = await self.quotations.get_price_summary(code)

            if self.mode == "backtest":
                if not self.backtest_lookup:
                    raise ValueError("Backtest 모드에서는 backtest_lookup 함수가 필요합니다.")
                after_price = await self.backtest_lookup(code)
            else:
                after_price = await self.quotations.get_current_price_value(code)

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
