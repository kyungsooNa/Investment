# strategies/GapUpPullback_strategy.py

import logging
from interfaces.strategy import Strategy
from typing import List, Dict, Optional

class GapUpPullbackStrategy(Strategy):
    def __init__(
        self,
        broker,
        min_gap_rate: float = 5.0,
        max_pullback_rate: float = 2.0,
        rebound_rate: float = 2.0,
        mode: str = "live",
        logger: Optional[logging.Logger] = None
    ):
        self.broker = broker
        self.min_gap_rate = min_gap_rate
        self.max_pullback_rate = max_pullback_rate
        self.rebound_rate = rebound_rate
        self.mode = mode
        self.logger = logger or logging.getLogger(__name__)

    async def run(self, stock_codes: List[str]) -> Dict:
        selected = []
        rejected = []

        for code in stock_codes:
            summary = await self.broker.get_price_summary(code)
            name = await self.broker.get_name_by_code(code)
            display = f"{name}({code})" if name else code

            previous_close = summary.get("prev_close")
            open_price = summary.get("open")
            low = summary.get("low")
            current = summary.get("current")

            if not all([previous_close, open_price, low, current]):
                self.logger.warning(f"[데이터 누락] {display} - 필수 가격 정보 없음")
                continue

            gap_up = (open_price - previous_close) / previous_close * 100
            pullback = (open_price - low) / open_price * 100
            rebound = (current - low) / low * 100

            is_candidate = (
                gap_up >= self.min_gap_rate and
                pullback >= self.max_pullback_rate and
                rebound >= self.rebound_rate
            )

            if is_candidate:
                selected.append({"code": code, "name": name})
                self.logger.info(
                    f"[후보 선정] {display} | 전일종가: {previous_close} | 시가: {open_price} | 저가: {low} | 종가: {current} | "
                    f"갭상승: {gap_up:.2f}% | 눌림: {pullback:.2f}% | 반등: {rebound:.2f}%"
                )
            else:
                rejected.append({"code": code, "name": name})
                self.logger.info(
                    f"[제외] {display} | 전일종가: {previous_close} | 시가: {open_price} | 저가: {low} | 종가: {current} | "
                    f"갭상승: {gap_up:.2f}% | 눌림: {pullback:.2f}% | 반등: {rebound:.2f}%"
                )

        return {
            "gapup_pullback_selected": selected,
            "gapup_pullback_rejected": rejected
        }
