from typing import List, Dict, Callable, Awaitable


class MomentumStrategyClassifier:
    def __init__(self, price_lookup: Callable[[str], Awaitable[dict]], logger=None):
        """
        :param price_lookup: 종목 코드를 받아 시가, 현재가, 등락률 정보를 반환하는 비동기 함수
        :param logger: 선택적 로거
        """
        self.price_lookup = price_lookup
        self.logger = logger

    async def classify_momentum_follow_through(
        self,
        symbols: List[str],
        min_change_rate: float = 10.0,
        min_follow_through: float = 2.0
    ) -> Dict[str, List[str]]:
        results = {"follow_through": [], "not_follow_through": []}

        for symbol in symbols:
            summary = await self.price_lookup(symbol)
            if self.logger:
                self.logger.debug(f"{symbol} summary: {summary}")

            change_rate = summary.get("change_rate", 0)
            after_rate = summary.get("after_rate", 0)

            if change_rate >= min_change_rate:
                if after_rate >= min_follow_through:
                    results["follow_through"].append(symbol)
                else:
                    results["not_follow_through"].append(symbol)

        return results
