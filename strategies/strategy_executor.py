# strategies/strategy_executor.py
from interfaces.strategy import Strategy
from typing import List, Dict


class StrategyExecutor:
    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    async def execute(self, stock_codes: List[str]) -> Dict:
        return await self.strategy.run(stock_codes)