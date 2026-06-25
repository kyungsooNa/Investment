# strategies/inverse_etf_regime_backtest.py
"""인버스 ETF 레짐 슬리브 일봉 백테스트 (R-2 Phase 2).

`InverseEtfRegimeStrategy`(라이브)의 진입/청산 논리를 일봉으로 근사해
다중 하락 사이클에서 PnL·MDD를 정량화한다. 데이터 소스 무관
(`run(index_bars, inverse_bars)`)이라 합성/실데이터(FDR) 양쪽에 쓴다.

레짐 게이트(엔진 근사):
  지수(index) bear = 종가 < regime_ma_period MA **AND** MA가 하락(전일 MA보다 낮음).
  라이브는 `MarketRegimeService`(hard_decline 판정)를 쓰지만, 백테스트는 동일 의도의
  MA-크로스+하락기울기 proxy로 단순화한다(외부 의존 없이 재현 가능).

진입: 해당일 지수 bear **AND** 인버스 종가 > 인버스 trend_ma_period MA → 종가 진입.

청산(우선순위 — 일중 가격 기반이 종가 레짐보다 선행):
  (1) hard stop  — 저가 ≤ 진입가×(1+stop/100). gap-through: 시가가 stop 아래면 시가 체결.
  (2) trailing   — 고점×(1−trailing/100) 하향 이탈(이익 구간에서만).
  (3) regime     — 종가 시점 지수가 bear 이탈 → 종가 청산.
  (4) terminal   — 데이터 끝까지 보유 시 마지막 봉 종가 청산.

비용: **ETF는 증권거래세(0.2%) 비과세** → round_trip_cost_pct 기본값은 위탁수수료
+슬리피지만 반영한 소액(0.1%)으로, 주식 round-trip(0.2%, 거래세 포함)보다 작다.
"""
import logging
from typing import Any, Dict, List, Optional


class InverseEtfRegimeBacktest:
    def __init__(
        self,
        regime_ma_period: int = 20,
        trend_ma_period: int = 20,
        stop_loss_pct: float = -5.0,
        trailing_stop_pct: Optional[float] = 8.0,
        round_trip_cost_pct: float = 0.1,
        logger: Optional[logging.Logger] = None,
    ):
        self._regime_ma_period = int(regime_ma_period)
        self._trend_ma_period = int(trend_ma_period)
        self._stop_loss_pct = float(stop_loss_pct)
        self._trailing_stop_pct = trailing_stop_pct
        self.round_trip_cost_pct = float(round_trip_cost_pct)
        self._logger = logger if logger else logging.getLogger(__name__)

    # ── public ──────────────────────────────────────────────────────

    def run(self, index_bars: List[Dict[str, Any]], inverse_bars: List[Dict[str, Any]]) -> Dict[str, Any]:
        """지수/인버스 일봉(오름차순)에 레짐 슬리브 시뮬을 적용해 거래/요약 반환."""
        inv = [b for b in (inverse_bars or []) if isinstance(b, dict)]
        bear_by_date = self._bear_flags(index_bars or [])
        inv_ma = self._ma_by_date(inv, self._trend_ma_period)

        trades: List[Dict[str, Any]] = []
        n = len(inv)
        i = self._trend_ma_period
        while i < n:
            date_i = inv[i].get("date")
            close_i = self._f(inv[i].get("close"))
            ma_i = inv_ma.get(date_i)
            # 진입 게이트: 지수 bear + 인버스 추세 확인
            if not bear_by_date.get(date_i, False) or ma_i is None or close_i <= ma_i or close_i <= 0:
                i += 1
                continue

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
                o = self._f(inv[j].get("open"))
                h = self._f(inv[j].get("high"))
                low = self._f(inv[j].get("low"))
                c = self._f(inv[j].get("close"))

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

                # (3) regime flip — 종가 시점 bear 이탈
                if not bear_by_date.get(inv[j].get("date"), False):
                    exit_price, exit_reason, exit_idx = c, "regime", j
                    break
                j += 1

            # (4) terminal — 미청산 시 마지막 봉 종가 강제청산
            if exit_price is None:
                exit_price = self._f(inv[n - 1].get("close"))
                exit_reason, exit_idx = "terminal", n - 1

            gross = (exit_price / entry_price - 1) * 100 if entry_price else 0.0
            net = gross - self.round_trip_cost_pct
            trades.append({
                "entry_date": inv[i].get("date"),
                "entry_price": entry_price,
                "exit_date": inv[exit_idx].get("date"),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "holding_days": holding if exit_reason != "terminal" else (exit_idx - i),
                "gross_return_pct": gross,
                "net_return_pct": net,
            })
            i = exit_idx + 1  # 포지션 중복 없음

        return {"trades": trades, "summary": self._summarize(trades)}

    def run_periods(
        self,
        index_bars: List[Dict[str, Any]],
        inverse_bars: List[Dict[str, Any]],
        periods: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """라벨별 날짜 윈도우([{label,start,end}])로 잘라 기간별 + 전체 요약 반환."""
        out_periods: List[Dict[str, Any]] = []
        all_trades: List[Dict[str, Any]] = []
        for p in periods or []:
            start, end = p.get("start", ""), p.get("end", "")
            idx_slice = self._slice(index_bars, start, end)
            inv_slice = self._slice(inverse_bars, start, end)
            res = self.run(idx_slice, inv_slice)
            all_trades.extend(res["trades"])
            out_periods.append({
                "label": p.get("label", f"{start}~{end}"),
                "start": start, "end": end,
                "summary": res["summary"],
                "trades": res["trades"],
            })
        return {"periods": out_periods, "overall": self._summarize(all_trades)}

    # ── internals ───────────────────────────────────────────────────

    def _bear_flags(self, index_bars: List[Dict[str, Any]]) -> Dict[str, bool]:
        """지수 일봉 → {date: is_bear}. bear = 종가 < MA AND MA 하락 기울기."""
        rows = [b for b in index_bars if isinstance(b, dict)]
        ma_by_date = self._ma_by_date(rows, self._regime_ma_period)
        flags: Dict[str, bool] = {}
        prev_ma: Optional[float] = None
        for r in rows:
            date = r.get("date")
            ma = ma_by_date.get(date)
            close = self._f(r.get("close"))
            if ma is None:
                flags[date] = False
            else:
                flags[date] = (close < ma) and (prev_ma is not None and ma < prev_ma)
                prev_ma = ma
        return flags

    def _ma_by_date(self, rows: List[Dict[str, Any]], period: int) -> Dict[str, Optional[float]]:
        """오름차순 일봉의 종가 SMA(기간) → {date: ma}. 데이터 부족 구간은 None."""
        out: Dict[str, Optional[float]] = {}
        closes: List[float] = []
        for r in rows:
            closes.append(self._f(r.get("close")))
            if len(closes) >= period:
                out[r.get("date")] = sum(closes[-period:]) / period
            else:
                out[r.get("date")] = None
        return out

    @staticmethod
    def _slice(bars: List[Dict[str, Any]], start: str, end: str) -> List[Dict[str, Any]]:
        return [
            b for b in (bars or [])
            if isinstance(b, dict)
            and (not start or str(b.get("date", "")) >= start)
            and (not end or str(b.get("date", "")) <= end)
        ]

    def _summarize(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(trades)
        if n == 0:
            return {"total_trades": 0, "wins": 0, "win_rate": 0.0,
                    "avg_net_return_pct": 0.0, "total_net_return_pct": 0.0,
                    "min_net_return_pct": 0.0, "max_drawdown_pct": 0.0,
                    "compound_return_pct": 0.0}
        nets = [t["net_return_pct"] for t in trades]
        wins = sum(1 for x in nets if x > 0)
        # 거래 시퀀스 복리 자산곡선 기준 MDD
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for x in nets:
            equity *= (1 + x / 100.0)
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak * 100 if peak else 0.0
            if dd < max_dd:
                max_dd = dd
        return {
            "total_trades": n,
            "wins": wins,
            "win_rate": wins / n,
            "avg_net_return_pct": sum(nets) / n,
            "total_net_return_pct": sum(nets),
            "min_net_return_pct": min(nets),
            "max_drawdown_pct": max_dd,
            "compound_return_pct": (equity - 1.0) * 100,
        }

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x if x is not None else 0)
        except (TypeError, ValueError):
            return 0.0
