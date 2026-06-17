# strategies/multiday_daily_breakout_backtest.py
"""멀티데이 일봉 돌파 백테스트 (R-1 생존편향 PnL 정량화용).

생존편향 PnL 비용은 같은날 청산 전략(VBO)이 아니라 **붕괴를 들고 가는
오버나잇/멀티데이 전략**(Oneil 계열·Larry Williams 채널 돌파)에 집중된다.
이를 정량화하려면 (1) 멀티데이 보유, (2) 상폐 종말 손실(거래정지·정리매매
갭다운)을 포착하는 일봉 시뮬이 필요하다.

엔진(데이터 소스 무관, `run_symbol(bars)`):
  진입 : 종가 > 직전 N일 신고가 (종가 베팅 돌파; 채널 돌파/Oneil 돌파 근사)
  청산 : (1) hard stop  — 저가 ≤ 진입가×(1+stop/100). **gap-through**: 시가가
             stop 아래로 갭다운하면 stop이 아닌 시가에 체결(R-4 동일 원리 →
             상폐 정리매매 붕괴를 손실로 반영).
         (2) trailing stop — 고점×(1−trailing/100) 하향 이탈(이익 구간에서만).
         (3) time stop — 보유일수 ≥ time_stop_days 시 종가 청산.
         (4) terminal — 데이터 끝(상폐일/기간말)까지 보유 시 마지막 봉 종가 청산
             (상폐 CSV는 정리매매 floor까지 담겨 있어 붕괴가 종가에 반영됨).

같은날 청산 전략이 아니므로 보유 중 발생하는 종말 붕괴를 손실로 잡는다.
한계: 거래정지로 정리매매조차 못 한 경우의 추가 손실은 미반영(마지막 거래
종가까지만) — 즉 **생존편향 비용을 오히려 보수적으로 과소평가**할 수 있다.
"""
import logging
from typing import Any, Dict, List, Optional


class MultiDayDailyBreakoutBacktest:
    def __init__(
        self,
        breakout_lookback: int = 20,
        stop_loss_pct: float = -5.0,
        trailing_stop_pct: Optional[float] = 8.0,
        time_stop_days: Optional[int] = 20,
        round_trip_cost_pct: float = 0.2,
        logger: Optional[logging.Logger] = None,
    ):
        self._lookback = int(breakout_lookback)
        self._stop_loss_pct = float(stop_loss_pct)
        self._trailing_stop_pct = trailing_stop_pct
        self._time_stop_days = time_stop_days
        self._cost_pct = float(round_trip_cost_pct)
        self._logger = logger if logger else logging.getLogger(__name__)

    def run_symbol(self, bars: List[Dict[str, Any]]) -> Dict[str, Any]:
        """단일 종목 일봉(오름차순)에 멀티데이 돌파 시뮬을 적용해 거래/요약 반환."""
        rows = [b for b in (bars or []) if isinstance(b, dict)]
        trades: List[Dict[str, Any]] = []
        n = len(rows)
        i = self._lookback
        while i < n:
            prior_high = max(
                (self._f(rows[j].get("high")) for j in range(i - self._lookback, i)),
                default=0.0,
            )
            close_i = self._f(rows[i].get("close"))
            if prior_high <= 0 or close_i <= prior_high:
                i += 1
                continue

            # 진입: 돌파일 종가 베팅
            entry_price = close_i
            stop_price = entry_price * (1 + self._stop_loss_pct / 100.0)
            peak = entry_price
            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None
            exit_idx = n - 1
            holding = 0

            j = i + 1
            while j < n:
                holding += 1
                o = self._f(rows[j].get("open"))
                h = self._f(rows[j].get("high"))
                low = self._f(rows[j].get("low"))
                c = self._f(rows[j].get("close"))

                # (1) hard stop — gap-through 시 시가 체결
                if low <= stop_price:
                    exit_price = o if (0 < o < stop_price) else stop_price
                    exit_reason, exit_idx = "stop", j
                    break

                # (2) trailing stop — 이익 구간에서만(trail이 hard stop 위일 때)
                if h > peak:
                    peak = h
                if self._trailing_stop_pct:
                    trail = peak * (1 - float(self._trailing_stop_pct) / 100.0)
                    if trail > stop_price and low <= trail:
                        exit_price = o if (0 < o < trail) else trail
                        exit_reason, exit_idx = "trailing", j
                        break

                # (3) time stop
                if self._time_stop_days and holding >= int(self._time_stop_days):
                    exit_price, exit_reason, exit_idx = c, "time", j
                    break
                j += 1

            # (4) terminal — 미청산 시 마지막 봉 종가 강제청산(상폐/기간말)
            if exit_price is None:
                exit_price = self._f(rows[n - 1].get("close"))
                exit_reason, exit_idx = "terminal", n - 1

            gross = (exit_price / entry_price - 1) * 100 if entry_price else 0.0
            net = gross - self._cost_pct
            trades.append({
                "entry_date": rows[i].get("date"),
                "entry_price": entry_price,
                "exit_date": rows[exit_idx].get("date"),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "holding_days": holding if exit_reason != "terminal" else (exit_idx - i),
                "gross_return_pct": gross,
                "net_return_pct": net,
            })
            i = exit_idx + 1  # 포지션 중복 없음 — 청산 이후부터 재탐색

        return {"trades": trades, "summary": self._summarize(trades)}

    def _summarize(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(trades)
        if n == 0:
            return {"total_trades": 0, "wins": 0, "win_rate": 0.0,
                    "avg_net_return_pct": 0.0, "total_net_return_pct": 0.0,
                    "min_net_return_pct": 0.0}
        nets = [t["net_return_pct"] for t in trades]
        wins = sum(1 for x in nets if x > 0)
        return {
            "total_trades": n,
            "wins": wins,
            "win_rate": wins / n,
            "avg_net_return_pct": sum(nets) / n,
            "total_net_return_pct": sum(nets),
            "min_net_return_pct": min(nets),
        }

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x if x is not None else 0)
        except (TypeError, ValueError):
            return 0.0
