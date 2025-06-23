# interfaces/strategy.py
from typing import List, Dict

class Strategy:
    async def run(self, stock_codes: List[str]) -> Dict:
        raise NotImplementedError
