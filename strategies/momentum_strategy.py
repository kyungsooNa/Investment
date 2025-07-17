import logging
from interfaces.strategy import Strategy
from typing import List, Dict, Optional, Callable
from common.types import ErrorCode, ResCommonResponse
import inspect


class MomentumStrategy(Strategy):
    def __init__(
        self,
        broker,
        min_change_rate: float = 10.0,
        min_follow_through: float = 3.0,
        min_follow_through_time: int = 5,  # 몇 분 후 기준인지
        mode: str = "live",  # 'live' or 'backtest'
        backtest_lookup: Optional[Callable[[str, Dict, int], int]] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.broker = broker  # ✅ 통합 wrapper (API + CSV)
        self.min_change_rate = min_change_rate
        self.min_follow_through = min_follow_through
        self.min_follow_through_time = min_follow_through_time  # 추가
        self.mode = mode
        self.backtest_lookup = backtest_lookup
        self.logger = logger or logging.getLogger(__name__)

    async def run(self, stock_codes: List[str]) -> Dict:
        results = []

        for code in stock_codes:
            summary : ResCommonResponse = await self.broker.get_price_summary(code)  # ✅ wrapper 통해 조회

            if self.mode == "backtest":
                if not self.backtest_lookup:
                    raise ValueError("Backtest 모드에서는 backtest_lookup 함수가 필요합니다.")

                result = self.backtest_lookup(
                    code,
                    summary.data,
                    self.min_follow_through_time
                )
                after_price = await result if inspect.isawaitable(result) else result
            else:
                price_data : ResCommonResponse = await self.broker.get_current_price(code)  # ✅ wrapper 통해 조회
                after_price = price_data.data.get("stck_prpr",0)

            summary.data["after"] = after_price
            summary.data["after_rate"] = (
                (after_price - summary.data["current"]) / summary.data["current"] * 100
                if summary.data["current"] else 0
            )
            results.append(summary.data)

        follow_through = []
        not_follow_through = []


        for s in results:
            code = s["symbol"]
            name : str = await self.broker.get_name_by_code(code)
            display = f"{name}({code})" if name else code

            # ▼▼▼▼▼ 핵심 로직 및 로그 수정 ▼▼▼▼▼
            # 1. 초기 모멘텀 (시가 대비 현재가) 조건 확인
            initial_momentum_ok = s["change_rate"] >= self.min_change_rate
            # 2. 추세 지속 (현재가 대비 N분 후 가격) 조건 확인
            follow_through_ok = s["after_rate"] >= self.min_follow_through

            is_success = initial_momentum_ok and follow_through_ok

            # 이제 로그에 각 단계의 실제값과 기준을 명확히 기록합니다.
            log_initial_rate = f"초기 등락률: {s['change_rate']:.2f}% (기준: {self.min_change_rate}%)"
            # ▼▼▼▼▼ 여기가 핵심 수정 부분입니다 ▼▼▼▼▼
            # N분 전/후 가격을 로그에 포함시킵니다.
            before_price = s['current']
            after_price = s['after']
            log_follow_rate = (
                f"{self.min_follow_through_time}분 후 상승률: {s['after_rate']:.2f}% "
                f"({before_price:,}원 → {after_price:,}원, 기준: {self.min_follow_through}%)"
            )
            # ▲▲▲▲▲ 여기가 핵심 수정 부분입니다 ▲▲▲▲▲
            if is_success:
                follow_through.append({"code": code, "name": name})
                self.logger.info(
                    f"[성공] 종목: {display} | {log_initial_rate} | {log_follow_rate}"
                )
            else:
                failure_reasons = []
                if not initial_momentum_ok:
                    failure_reasons.append("초기 등락률 미달")
                if not follow_through_ok:
                    failure_reasons.append("추세 지속 실패")

                reason_str = " & ".join(failure_reasons)
                not_follow_through.append({"code": code, "name": name})
                self.logger.info(
                    f"[실패] 종목: {display} | 사유: {reason_str} | {log_initial_rate} | {log_follow_rate}"
                )
            # ▲▲▲▲▲ 핵심 로직 및 로그 수정 ▲▲▲▲▲

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
