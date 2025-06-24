import logging
from interfaces.strategy import Strategy
from typing import List, Dict, Optional, Callable

class MomentumStrategy(Strategy):
    def __init__(
        self,
        quotations,
        min_change_rate: float = 10.0,
        min_follow_through: float = 3.0,
        min_follow_through_time: int = 5,  # 몇 분 후 기준인지
        mode: str = "live",  # 'live' or 'backtest'
        backtest_lookup: Optional[Callable[[str], int]] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.quotations = quotations
        self.min_change_rate = min_change_rate
        self.min_follow_through = min_follow_through
        self.min_follow_through_time = min_follow_through_time  # 추가
        self.mode = mode
        self.backtest_lookup = backtest_lookup
        self.logger = logger or logging.getLogger(__name__)

    async def run(self, stock_codes: List[str]) -> Dict:
        results = []

        for code in stock_codes:
            summary = await self.quotations.get_price_summary(code)

            if self.mode == "backtest":
                if not self.backtest_lookup:
                    raise ValueError("Backtest 모드에서는 backtest_lookup 함수가 필요합니다.")
                after_price = await self.backtest_lookup(code)
            else:
                price_data = await self.quotations.get_current_price(code)
                after_price = int(price_data.get("output", {}).get("stck_prpr", 0))

            summary["after"] = after_price
            summary["after_rate"] = (
                (after_price - summary["current"]) / summary["current"] * 100
                if summary["current"] else 0
            )
            results.append(summary)

        follow_through = []
        not_follow_through = []

        for s in results:
            code = s["symbol"]
            name = await self.quotations.get_stock_name_by_code(code)  # ✅ 종목명 조회
            display = f"{name}({code})" if name else code  # ✅ 종목명(종목코드) 또는 코드만

            is_success = (
                s["change_rate"] >= self.min_change_rate and
                s["after_rate"] >= self.min_follow_through
            )

            if is_success:
                follow_through.append({"code": code, "name": name})
                self.logger.info(
                    f"[성공] 종목: {display} | 시가: {s['open']} | 종가: {s['current']} | "
                    f"등락률: {s['change_rate']:.2f}% | 기준 등락률: {self.min_change_rate}% | "
                    f"{self.min_follow_through_time}분 후 상승률: {s['after_rate']:.2f}% | "
                    f"기준 상승률: {self.min_follow_through}% | 모드: {self.mode}"
                )
            else:
                not_follow_through.append({"code": code, "name": name})
                self.logger.info(
                    f"[실패] 종목: {display} | 시가: {s['open']} | 종가: {s['current']} | "
                    f"등락률: {s['change_rate']:.2f}% | 기준 등락률: {self.min_change_rate}% | "
                    f"{self.min_follow_through_time}분 후 상승률: {s['after_rate']:.2f}% | "
                    f"기준 상승률: {self.min_follow_through}% | 모드: {self.mode}"
                )

        total = len(results)
        success = len(follow_through)
        fail = len(not_follow_through)
        success_rate = (success / total * 100) if total > 0 else 0

        self.logger.info(
            f"[결과 요약] 총 종목: {total}, 성공: {success}, 실패: {fail}, 성공률: {success_rate:.2f}%, 모드: {self.mode}"
        )

        return {
            "follow_through": follow_through,  # [{"code": "005930", "name": "삼성전자"}, ...]
            "not_follow_through": not_follow_through
        }
