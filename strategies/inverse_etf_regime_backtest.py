# strategies/inverse_etf_regime_backtest.py
"""인버스 ETF 레짐 슬리브 일봉 백테스트 (R-2 Phase 2).

`InverseEtfRegimeStrategy`(라이브)의 진입/청산 논리를 일봉으로 근사해
다중 하락 사이클에서 PnL·MDD를 정량화한다. 데이터 소스 무관
(`run(index_bars, inverse_bars)`)이라 합성/실데이터(FDR) 양쪽에 쓴다.

레짐 게이트(엔진 근사):
  지수(index) bear = 종가 < regime_ma_period MA **AND** MA가 하락(전일 MA보다 낮음).
  라이브는 `MarketRegimeService`(hard_decline 판정)를 쓰지만, 백테스트는 동일 의도의
  MA-크로스+하락기울기 proxy로 단순화한다(외부 의존 없이 재현 가능).

진입: 해당일 지수 bear **AND** 인버스 종가 > 인버스 trend_ma_period MA
  **AND** 종가 > 시가 **AND** 전일 대비 상승
  **AND** 외국인+기관 순매도 **AND** 삼성전자·SK하이닉스 모두 음봉·전일 대비 하락
  → 종가 진입. 확인 데이터가 없으면 라이브와 같이 fail-closed 한다.
  라이브의 실제 매도 1호가는 과거 일봉에 없으므로 종가로 근사하며, 슬리피지는 비용에 포함한다.

청산(라이브 우선순위):
  (1) regime     — 종가 시점 지수가 bear 이탈 → 종가 청산.
  (2) hard stop  — 저가가 비용 반영 net 손절선 이하. gap-through: 시가 체결.
  (3) intraday   — 시가 대비 intraday_open_breach_pct 이하이면서
                   전일 대비 intraday_invalidation_pct 이하 → 종가 청산.
  (4) trailing   — 고점×(1−trailing/100) 하향 이탈(이익 구간에서만).
  (5) partial    — 비용 반영 목표 순수익률 도달 시 지정 비율 1회 익절.
  (6) terminal   — 데이터 끝까지 보유 시 마지막 봉 종가 청산.

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
        intraday_invalidation_pct: float = -2.0,
        intraday_open_breach_pct: float = -0.3,
        trailing_stop_pct: Optional[float] = 8.0,
        take_profit_pct: Optional[float] = 12.0,
        partial_sell_ratio: float = 0.5,
        round_trip_cost_pct: float = 0.1,
        smart_money_filter_enabled: bool = True,
        max_smart_money_net_buy_100m: float = 0.0,
        semiconductor_filter_enabled: bool = True,
        semiconductor_codes: tuple[str, ...] = ("005930", "000660"),
        logger: Optional[logging.Logger] = None,
    ):
        self._regime_ma_period = int(regime_ma_period)
        self._trend_ma_period = int(trend_ma_period)
        self._stop_loss_pct = float(stop_loss_pct)
        self._intraday_invalidation_pct = float(intraday_invalidation_pct)
        self._intraday_open_breach_pct = float(intraday_open_breach_pct)
        self._trailing_stop_pct = trailing_stop_pct
        self._take_profit_pct = take_profit_pct
        self._partial_sell_ratio = float(partial_sell_ratio)
        self.round_trip_cost_pct = float(round_trip_cost_pct)
        self._smart_money_filter_enabled = bool(smart_money_filter_enabled)
        self._max_smart_money_net_buy_100m = float(max_smart_money_net_buy_100m)
        self._semiconductor_filter_enabled = bool(semiconductor_filter_enabled)
        self._semiconductor_codes = tuple(semiconductor_codes)
        self._logger = logger if logger else logging.getLogger(__name__)

    # ── public ──────────────────────────────────────────────────────

    def run(
        self,
        index_bars: List[Dict[str, Any]],
        inverse_bars: List[Dict[str, Any]],
        *,
        investor_flows: Optional[List[Dict[str, Any]]] = None,
        semiconductor_bars: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """일봉과 확인 데이터에 레짐 슬리브 시뮬을 적용해 거래/요약을 반환한다."""
        inv = [b for b in (inverse_bars or []) if isinstance(b, dict)]
        bear_by_date = self._bear_flags(index_bars or [])
        inv_ma = self._ma_by_date(inv, self._trend_ma_period)
        flow_by_date = self._rows_by_date(investor_flows or [])
        semi_by_code = {
            code: self._bars_with_previous_close((semiconductor_bars or {}).get(code, []))
            for code in self._semiconductor_codes
        }
        coverage = {
            "candidate_dates": 0,
            "smart_money_available_dates": 0,
            "semiconductor_available_dates": 0,
            "fully_available_dates": 0,
            "confirmed_dates": 0,
        }

        trades: List[Dict[str, Any]] = []
        n = len(inv)
        i = self._trend_ma_period
        while i < n:
            date_i = inv[i].get("date")
            close_i = self._f(inv[i].get("close"))
            open_i = self._f(inv[i].get("open"))
            prev_close_i = self._f(inv[i - 1].get("close")) if i > 0 else 0.0
            ma_i = inv_ma.get(date_i)
            # 진입 게이트: 지수 bear + 인버스 일봉/장중 추세 확인
            if (
                not bear_by_date.get(date_i, False)
                or ma_i is None
                or close_i <= ma_i
                or close_i <= open_i
                or prev_close_i <= 0
                or close_i <= prev_close_i
            ):
                i += 1
                continue

            coverage["candidate_dates"] += 1
            confirmation = self._confirmation_status(date_i, flow_by_date, semi_by_code)
            if confirmation["smart_money_available"]:
                coverage["smart_money_available_dates"] += 1
            if confirmation["semiconductor_available"]:
                coverage["semiconductor_available_dates"] += 1
            if confirmation["fully_available"]:
                coverage["fully_available_dates"] += 1
            if not confirmation["confirmed"]:
                i += 1
                continue
            coverage["confirmed_dates"] += 1

            entry_price = close_i
            # 라이브는 net PnL로 손절하므로 고정 round-trip 비용을 가격 임계값에 역반영한다.
            stop_gross_pct = self._stop_loss_pct + self.round_trip_cost_pct
            stop_price = entry_price * (1 + stop_gross_pct / 100.0)
            peak = entry_price
            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None
            exit_idx = n - 1
            holding = 0
            partial_exit_price: Optional[float] = None
            partial_exit_fraction = 0.0
            realized_gross = 0.0
            remaining_fraction = 1.0

            j = i + 1
            while j < n:
                holding += 1
                o = self._f(inv[j].get("open"))
                h = self._f(inv[j].get("high"))
                low = self._f(inv[j].get("low"))
                c = self._f(inv[j].get("close"))
                prev_close = self._f(inv[j - 1].get("close")) if j > 0 else 0.0
                change_pct = ((c / prev_close) - 1) * 100 if prev_close > 0 else 0.0

                # (1) regime flip — 라이브와 같이 다른 가격 청산보다 우선
                if not bear_by_date.get(inv[j].get("date"), False):
                    exit_price, exit_reason, exit_idx = c, "regime", j
                    realized_gross += remaining_fraction * (exit_price / entry_price - 1)
                    break

                # (2) hard stop — gap-through 시 시가 체결
                if low <= stop_price:
                    exit_price = o if (0 < o < stop_price) else stop_price
                    exit_reason, exit_idx = "stop", j
                    realized_gross += remaining_fraction * (exit_price / entry_price - 1)
                    break

                # (3) 장중 추세 무효화 — 라이브 현재가를 일봉 종가로 근사
                open_breach_pct = ((c / o) - 1) * 100 if o > 0 else 0.0
                if (
                    o > 0
                    and open_breach_pct <= self._intraday_open_breach_pct
                    and change_pct <= self._intraday_invalidation_pct
                ):
                    exit_price, exit_reason, exit_idx = c, "intraday_invalidation", j
                    realized_gross += remaining_fraction * (exit_price / entry_price - 1)
                    break

                # (4) trailing stop — 이익 구간에서만(trail이 hard stop 위일 때)
                if h > peak:
                    peak = h
                if self._trailing_stop_pct:
                    trail = peak * (1 - float(self._trailing_stop_pct) / 100.0)
                    if trail > stop_price and low <= trail:
                        exit_price = o if (0 < o < trail) else trail
                        exit_reason, exit_idx = "trailing", j
                        realized_gross += remaining_fraction * (exit_price / entry_price - 1)
                        break

                # (5) 목표 순수익률 도달 시 1회 부분익절
                if self._take_profit_pct is not None and partial_exit_price is None:
                    target_gross_pct = float(self._take_profit_pct) + self.round_trip_cost_pct
                    target_price = entry_price * (1 + target_gross_pct / 100.0)
                    if h >= target_price:
                        fraction = min(max(self._partial_sell_ratio, 0.0), 1.0)
                        if fraction > 0:
                            partial_exit_price = target_price
                            partial_exit_fraction = fraction
                            realized_gross += fraction * (target_price / entry_price - 1)
                            remaining_fraction -= fraction
                            if remaining_fraction <= 0:
                                exit_price = target_price
                                exit_reason, exit_idx = "take_profit", j
                                break
                j += 1

            # (6) terminal — 미청산 시 마지막 봉 종가 강제청산
            if exit_price is None:
                exit_price = self._f(inv[n - 1].get("close"))
                exit_reason, exit_idx = "terminal", n - 1
                realized_gross += remaining_fraction * (exit_price / entry_price - 1)

            gross = realized_gross * 100 if entry_price else 0.0
            net = gross - self.round_trip_cost_pct
            trades.append({
                "entry_date": inv[i].get("date"),
                "entry_price": entry_price,
                "exit_date": inv[exit_idx].get("date"),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "partial_exit_price": partial_exit_price,
                "partial_exit_fraction": partial_exit_fraction,
                "holding_days": holding if exit_reason != "terminal" else (exit_idx - i),
                "gross_return_pct": gross,
                "net_return_pct": net,
            })
            i = exit_idx + 1  # 포지션 중복 없음

        return {
            "trades": trades,
            "summary": self._summarize(trades),
            "confirmation_coverage": coverage,
        }

    def run_periods(
        self,
        index_bars: List[Dict[str, Any]],
        inverse_bars: List[Dict[str, Any]],
        periods: List[Dict[str, str]],
        *,
        investor_flows: Optional[List[Dict[str, Any]]] = None,
        semiconductor_bars: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """라벨별 날짜 윈도우([{label,start,end}])로 잘라 기간별 + 전체 요약 반환."""
        out_periods: List[Dict[str, Any]] = []
        all_trades: List[Dict[str, Any]] = []
        for p in periods or []:
            start, end = p.get("start", ""), p.get("end", "")
            idx_slice = self._slice(index_bars, start, end)
            inv_slice = self._slice(inverse_bars, start, end)
            res = self.run(
                idx_slice,
                inv_slice,
                investor_flows=investor_flows,
                semiconductor_bars=semiconductor_bars,
            )
            all_trades.extend(res["trades"])
            out_periods.append({
                "label": p.get("label", f"{start}~{end}"),
                "start": start, "end": end,
                "summary": res["summary"],
                "trades": res["trades"],
                "confirmation_coverage": res["confirmation_coverage"],
            })
        coverage_keys = (
            "candidate_dates",
            "smart_money_available_dates",
            "semiconductor_available_dates",
            "fully_available_dates",
            "confirmed_dates",
        )
        overall_coverage = {
            key: sum(p["confirmation_coverage"][key] for p in out_periods)
            for key in coverage_keys
        }
        return {
            "periods": out_periods,
            "overall": self._summarize(all_trades),
            "confirmation_coverage": overall_coverage,
        }

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

    def _confirmation_status(
        self,
        date: str,
        flow_by_date: Dict[str, Dict[str, Any]],
        semi_by_code: Dict[str, Dict[str, tuple[Dict[str, Any], Optional[float]]]],
    ) -> Dict[str, bool]:
        smart_available = not self._smart_money_filter_enabled
        smart_passed = not self._smart_money_filter_enabled
        if self._smart_money_filter_enabled:
            flow = flow_by_date.get(date)
            foreign = self._number_or_none(flow.get("foreign")) if flow else None
            institution = self._number_or_none(flow.get("institution")) if flow else None
            smart_available = foreign is not None and institution is not None
            smart_passed = bool(
                smart_available
                and foreign + institution < self._max_smart_money_net_buy_100m
            )

        semi_available = not self._semiconductor_filter_enabled
        semi_passed = not self._semiconductor_filter_enabled
        if self._semiconductor_filter_enabled:
            checks: List[tuple[bool, bool]] = []
            for code in self._semiconductor_codes:
                item = semi_by_code.get(code, {}).get(date)
                if not item:
                    checks.append((False, False))
                    continue
                bar, prev_close = item
                open_price = self._number_or_none(bar.get("open"))
                close = self._number_or_none(bar.get("close"))
                available = bool(
                    open_price is not None
                    and close is not None
                    and open_price > 0
                    and close > 0
                    and prev_close is not None
                    and prev_close > 0
                )
                checks.append((available, bool(
                    available and close < open_price and close < prev_close
                )))
            semi_available = bool(checks) and all(available for available, _ in checks)
            semi_passed = semi_available and all(passed for _, passed in checks)

        fully_available = smart_available and semi_available
        return {
            "smart_money_available": smart_available,
            "semiconductor_available": semi_available,
            "fully_available": fully_available,
            "confirmed": fully_available and smart_passed and semi_passed,
        }

    @staticmethod
    def _rows_by_date(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {
            str(row.get("date")): row
            for row in rows
            if isinstance(row, dict) and row.get("date")
        }

    @classmethod
    def _bars_with_previous_close(
        cls,
        rows: List[Dict[str, Any]],
    ) -> Dict[str, tuple[Dict[str, Any], Optional[float]]]:
        out: Dict[str, tuple[Dict[str, Any], Optional[float]]] = {}
        previous_close: Optional[float] = None
        valid_rows = sorted(
            (row for row in rows if isinstance(row, dict) and row.get("date")),
            key=lambda row: str(row.get("date")),
        )
        for row in valid_rows:
            out[str(row["date"])] = (row, previous_close)
            previous_close = cls._number_or_none(row.get("close"))
        return out

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

    @staticmethod
    def _number_or_none(x) -> Optional[float]:
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None
