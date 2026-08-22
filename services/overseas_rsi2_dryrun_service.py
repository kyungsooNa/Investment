"""해외 RSI2 Pullback dry-run 신호 서비스.

국내 `RSI2PullbackStrategy` 의 일봉 기반 평균회귀 아이디어 중 해외 데이터로
재현 가능한 부분만 적용한다. Minervini Stage 2 는 해외에 같은 소스가 없으므로
장기 상승추세(`close > 200MA`)로 대체한다. 주문 경로는 없고 shadow 저널에
would-be 신호만 기록한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


@dataclass
class OverseasRSI2Config:
    rsi_period: int = 2
    rsi_threshold: float = 10.0
    trend_ma_period: int = 200
    take_profit_ma_period: int = 5
    hard_stop_pct: float = -5.0
    min_history_days: int = 202


class OverseasRSI2DryRunService:
    STRATEGY_NAME = "RSI2Pullback_overseas"
    SIGNAL_SOURCE = "overseas_rsi2_dryrun"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasRSI2Config] = None,
        exchange: OverseasExchange = OverseasExchange.NASD,
        position_sizing_service=None,
        fx_provider=None,
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._journal = shadow_journal
        self._logger = logger or logging.getLogger(__name__)
        self._cfg = config or OverseasRSI2Config()
        self._default_exchange = exchange
        self._sizing_service = position_sizing_service
        self._fx_provider = fx_provider

    async def scan_dry_run(
        self,
        exchange: Optional[OverseasExchange] = None,
        *,
        top_n: Optional[int] = None,
        min_avg_trading_value: Optional[float] = None,
        record: bool = True,
    ) -> List[Dict[str, Any]]:
        """후보를 평가해 RSI2 BUY would-be 신호를 반환하고 shadow 저널에 기록한다."""
        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(
            ex, top_n=top_n, min_avg_trading_value=min_avg_trading_value
        )

        fx_rate = await self._resolve_fx_rate()
        signals: List[Dict[str, Any]] = []
        limit = max(self._cfg.trend_ma_period + self._cfg.rsi_period + 5, self._cfg.min_history_days)
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=limit, exchange=ex)
            except Exception as e:
                self._logger.warning({"event": "overseas_rsi2_ohlcv_error", "code": code, "error": str(e)})
                continue
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                continue

            sig = self._evaluate(code, cand, resp.data)
            if not sig:
                continue
            if self._sizing_service is not None:
                sizing = self._sizing_service.size(
                    limit_price_usd=sig["entry_price"], fx_krw_per_usd=fx_rate
                )
                sig["qty"] = sizing.get("qty")
                sig["notional_usd"] = sizing.get("notional_usd")
                if sizing.get("fx_krw_per_usd") is not None:
                    sig["fx_krw_per_usd"] = sizing.get("fx_krw_per_usd")
                if sizing.get("krw_exposure") is not None:
                    sig["krw_exposure"] = sizing.get("krw_exposure")
            signals.append(sig)
            if record and self._journal is not None:
                self._journal.record(
                    strategy_name=self.STRATEGY_NAME,
                    code=code,
                    signal=sig,
                    snapshot={
                        "exchange": ex.value,
                        "avg_trading_value": cand.get("avg_trading_value"),
                        "bar": self._bar_ohlc(resp.data[-1]),
                    },
                    signal_source=self.SIGNAL_SOURCE,
                )

        self._logger.info({
            "event": "overseas_rsi2_dryrun_scan",
            "exchange": ex.value,
            "candidates": len(candidates or []),
            "signals": len(signals),
        })
        return signals

    async def _resolve_fx_rate(self) -> Optional[float]:
        if self._sizing_service is None or self._fx_provider is None:
            return None
        try:
            rate = await self._fx_provider()
        except Exception as e:
            self._logger.warning({"event": "overseas_rsi2_fx_error", "error": str(e)})
            return None
        try:
            rate = float(rate) if rate is not None else None
        except (TypeError, ValueError):
            return None
        return rate if (rate is not None and rate > 0) else None

    def _evaluate(
        self,
        code: str,
        candidate: Dict[str, Any],
        rows: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        min_rows = max(self._cfg.min_history_days, self._cfg.trend_ma_period + self._cfg.rsi_period)
        if not rows or len(rows) < min_rows:
            return None

        closes = [self._f(r.get("close")) for r in rows]
        if any(v <= 0 for v in closes[-min_rows:]):
            return None
        cur = rows[-1]
        current = closes[-1]
        ma_200d = sum(closes[-self._cfg.trend_ma_period:]) / self._cfg.trend_ma_period
        if current <= ma_200d:
            return None

        rsi2 = self._rsi(closes, self._cfg.rsi_period)
        if rsi2 is None or rsi2 > self._cfg.rsi_threshold:
            return None

        ma_5d = sum(closes[-self._cfg.take_profit_ma_period:]) / self._cfg.take_profit_ma_period
        stop_price = current * (1 + self._cfg.hard_stop_pct / 100)
        confidence = max(0.0, min(1.0, (self._cfg.rsi_threshold - rsi2) / max(self._cfg.rsi_threshold, 1.0)))
        return {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "name": candidate.get("name", code),
            "action": "BUY",
            "date": cur.get("date"),
            "entry_price": current,
            "entry_reason": "overseas_rsi2_pullback",
            "rsi2": rsi2,
            "rsi_threshold": self._cfg.rsi_threshold,
            "ma_200d": ma_200d,
            "ma_5d": ma_5d,
            "target_ma_period": self._cfg.take_profit_ma_period,
            "stop_price": stop_price,
            "confidence": confidence,
            "reason": (
                f"RSI2진입(RSI({self._cfg.rsi_period})={rsi2:.2f} <= "
                f"{self._cfg.rsi_threshold:.1f}, 종가>200MA)"
            ),
        }

    @classmethod
    def _rsi(cls, closes: List[float], period: int) -> Optional[float]:
        if period <= 0 or len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-period:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _bar_ohlc(bar: Dict[str, Any]) -> Dict[str, Any]:
        return {k: bar.get(k) for k in ("date", "open", "high", "low", "close", "volume")}

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
