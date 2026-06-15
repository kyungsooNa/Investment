# strategies/overseas_daily_vbo_backtest.py
"""해외 일봉 VBO 백테스트 (Phase 2).

해외는 분봉/실시간 데이터가 없으므로 라이브 VBO를 그대로 재생할 수 없다.
대신 고전적 Larry Williams 변동성 돌파(일봉 근사)로 신호 유효성을 검증한다:

  prev_range = 전일고 - 전일저
  target     = 당일시가 + K × prev_range
  진입        : 당일고 >= target → target 에 매수
  청산        : 당일저 <= 손절가(진입가×(1+stop_loss_pct/100)) → 손절가 청산
                아니면 종가 청산 (당일 청산, 오버나잇 없음)

실주문/배선(Phase 3/4) 전 "해외에서 의미있는 진입/청산 신호를 내는가"를 확인하는
게이트용 경량 도구. 라이브 무거운 BacktestPeriodRunner 와는 별개(장중 microstructure 미사용).
"""
import logging
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


class OverseasDailyVBOBacktest:
    def __init__(
        self,
        k_value: float = 0.5,
        stop_loss_pct: float = -3.0,
        round_trip_cost_pct: float = 0.2,
        logger: Optional[logging.Logger] = None,
    ):
        self._k = k_value
        self._stop_loss_pct = stop_loss_pct
        self._cost_pct = round_trip_cost_pct
        self._logger = logger if logger else logging.getLogger(__name__)

    def run_symbol(self, bars: List[Dict[str, Any]]) -> Dict[str, Any]:
        """단일 종목 일봉(오름차순)에 일봉 VBO 근사를 적용해 거래/요약 반환."""
        trades: List[Dict[str, Any]] = []
        rows = [b for b in (bars or []) if isinstance(b, dict)]

        for i in range(1, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            prev_range = self._f(prev.get("high")) - self._f(prev.get("low"))
            if prev_range <= 0:
                continue
            open_ = self._f(cur.get("open"))
            high = self._f(cur.get("high"))
            low = self._f(cur.get("low"))
            close = self._f(cur.get("close"))
            if open_ <= 0:
                continue

            target = open_ + self._k * prev_range
            if high < target:
                continue  # 돌파 미발생 → 진입 없음

            entry_price = target
            stop_price = entry_price * (1 + self._stop_loss_pct / 100.0)
            if low <= stop_price:
                exit_price, exit_reason = stop_price, "stop"
            else:
                exit_price, exit_reason = close, "eod"

            gross = (exit_price / entry_price - 1) * 100
            net = gross - self._cost_pct
            trades.append({
                "date": cur.get("date"),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "gross_return_pct": gross,
                "net_return_pct": net,
            })

        return {"trades": trades, "summary": self._summarize(trades)}

    async def run_backtest(
        self,
        stock_query_service,
        symbols: List[str],
        exchange: OverseasExchange = OverseasExchange.NASD,
        *,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """심볼별 해외 일봉을 조회(Phase 1-1 어댑터)해 백테스트하고 집계한다."""
        per_symbol: Dict[str, Any] = {}
        all_trades: List[Dict[str, Any]] = []

        for symbol in symbols:
            try:
                resp = await stock_query_service.get_ohlcv_range(
                    symbol, "D", start_date, end_date, exchange=exchange
                )
            except Exception as e:
                self._logger.warning({"event": "overseas_backtest_fetch_error", "code": symbol, "error": str(e)})
                continue
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                continue
            result = self.run_symbol(resp.data)
            per_symbol[symbol] = result
            for t in result["trades"]:
                all_trades.append({**t, "symbol": symbol})

        return {"per_symbol": per_symbol, "trades": all_trades, "summary": self._summarize(all_trades)}

    def _summarize(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(trades)
        if n == 0:
            return {"total_trades": 0, "wins": 0, "win_rate": 0.0,
                    "avg_net_return_pct": 0.0, "total_net_return_pct": 0.0}
        nets = [t["net_return_pct"] for t in trades]
        wins = sum(1 for x in nets if x > 0)
        return {
            "total_trades": n,
            "wins": wins,
            "win_rate": wins / n,
            "avg_net_return_pct": sum(nets) / n,
            "total_net_return_pct": sum(nets),
        }

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
