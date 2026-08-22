"""해외 O'Neil Squeeze Breakout dry-run 신호 서비스.

국내 `OneilSqueezeBreakoutStrategy` 의 진입 조건 중 해외 완성 일봉으로 재현 가능한
스퀴즈, 20일 고점 돌파, 거래량 돌파, 캔들 품질만 적용한다. 프로그램 순매수와
체결강도는 국내 장중 전용 데이터라 제외하고, 주문 경로 없이 shadow 저널에 would-be
신호만 기록한다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


@dataclass
class OverseasSqueezeBreakoutConfig:
    bollinger_period: int = 20
    squeeze_lookback: int = 20
    squeeze_tolerance: float = 1.2
    breakout_high_period: int = 20
    breakout_min_buffer_pct: float = 0.0
    max_extension_pct: float = 5.0
    volume_breakout_multiplier: float = 1.5
    min_candle_relative_pos: float = 0.7
    stop_loss_pct: float = -5.0
    partial_profit_trigger_pct: float = 15.0


class OverseasSqueezeBreakoutDryRunService:
    STRATEGY_NAME = "O'NeilOSB_overseas"
    SIGNAL_SOURCE = "overseas_osb_dryrun"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasSqueezeBreakoutConfig] = None,
        exchange: OverseasExchange = OverseasExchange.NASD,
        position_sizing_service=None,
        fx_provider=None,
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._journal = shadow_journal
        self._logger = logger or logging.getLogger(__name__)
        self._cfg = config or OverseasSqueezeBreakoutConfig()
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
        """후보를 평가해 OSB BUY would-be 신호를 반환하고(선택) shadow 저널에 기록한다."""
        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(
            ex, top_n=top_n, min_avg_trading_value=min_avg_trading_value
        )

        fx_rate = await self._resolve_fx_rate()
        signals: List[Dict[str, Any]] = []
        limit = self._history_limit()
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=limit, exchange=ex)
            except Exception as e:
                self._logger.warning({"event": "overseas_osb_ohlcv_error", "code": code, "error": str(e)})
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
            "event": "overseas_osb_dryrun_scan",
            "exchange": ex.value,
            "candidates": len(candidates or []),
            "signals": len(signals),
        })
        return signals

    def _history_limit(self) -> int:
        return max(
            self._cfg.breakout_high_period,
            self._cfg.bollinger_period + self._cfg.squeeze_lookback,
        ) + 1

    async def _resolve_fx_rate(self) -> Optional[float]:
        if self._sizing_service is None or self._fx_provider is None:
            return None
        try:
            rate = await self._fx_provider()
        except Exception as e:
            self._logger.warning({"event": "overseas_osb_fx_error", "error": str(e)})
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
        min_rows = self._history_limit()
        if not rows or len(rows) < min_rows:
            return None

        cur = rows[-1]
        history = rows[:-1]
        current = self._f(cur.get("close"))
        volume = self._f(cur.get("volume"))
        if current <= 0 or volume <= 0:
            return None

        closes = [self._f(r.get("close")) for r in history]
        if len(closes) < self._cfg.bollinger_period + self._cfg.squeeze_lookback:
            return None
        if any(v <= 0 for v in closes[-(self._cfg.bollinger_period + self._cfg.squeeze_lookback):]):
            return None

        bb_width = self._bb_width(closes[-self._cfg.bollinger_period:])
        widths = [
            self._bb_width(closes[i - self._cfg.bollinger_period:i])
            for i in range(
                len(closes) - self._cfg.squeeze_lookback + 1,
                len(closes) + 1,
            )
        ]
        widths = [w for w in widths if w > 0]
        if not widths or bb_width <= 0:
            return None
        bb_width_min = min(widths)
        if bb_width > bb_width_min * self._cfg.squeeze_tolerance:
            return None

        high_rows = history[-self._cfg.breakout_high_period:]
        breakout_level = max((self._f(r.get("high")) for r in high_rows), default=0.0)
        breakout_threshold = breakout_level * (1 + self._cfg.breakout_min_buffer_pct / 100)
        if breakout_level <= 0 or current < breakout_threshold:
            return None
        max_entry = breakout_level * (1 + self._cfg.max_extension_pct / 100)
        if current > max_entry:
            return None

        volumes = [self._f(r.get("volume")) for r in history[-self._cfg.breakout_high_period:]]
        if len(volumes) < self._cfg.breakout_high_period or any(v <= 0 for v in volumes):
            return None
        avg_volume = sum(volumes) / len(volumes)
        if volume < avg_volume * self._cfg.volume_breakout_multiplier:
            return None

        high = self._f(cur.get("high"))
        low = self._f(cur.get("low"))
        day_range = high - low
        relative_pos = 1.0
        if day_range > 0:
            relative_pos = (current - low) / day_range
            if relative_pos < self._cfg.min_candle_relative_pos:
                return None

        stop_price = current * (1 + self._cfg.stop_loss_pct / 100)
        target_price = current * (1 + self._cfg.partial_profit_trigger_pct / 100)
        volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0
        return {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "name": candidate.get("name", code),
            "action": "BUY",
            "date": cur.get("date"),
            "entry_price": current,
            "entry_reason": "overseas_squeeze_breakout",
            "breakout_level": breakout_level,
            "volume": volume,
            "avg_volume": avg_volume,
            "volume_ratio": volume_ratio,
            "bb_width": bb_width,
            "bb_width_min_20d": bb_width_min,
            "candle_relative_pos": relative_pos,
            "stop_price": stop_price,
            "target_price": target_price,
            "excluded_filters": ["program_buy", "execution_strength", "market_timing"],
            "reason": (
                f"OSB진입(스퀴즈 {bb_width:.4f}/{bb_width_min:.4f}, "
                f"20일 돌파 {current:.2f}>={breakout_threshold:.2f}, "
                f"거래량 {volume_ratio:.2f}x, 위치 {relative_pos:.2f})"
            ),
        }

    @staticmethod
    def _bb_width(values: List[float]) -> float:
        vals = [float(v) for v in values if v > 0]
        if not vals:
            return 0.0
        mean = sum(vals) / len(vals)
        if mean <= 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = math.sqrt(variance)
        upper = mean + 2 * std
        lower = mean - 2 * std
        return (upper - lower) / mean

    @staticmethod
    def _bar_ohlc(bar: Dict[str, Any]) -> Dict[str, Any]:
        return {k: bar.get(k) for k in ("date", "open", "high", "low", "close", "volume")}

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
