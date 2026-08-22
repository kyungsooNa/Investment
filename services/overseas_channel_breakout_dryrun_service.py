"""해외 Larry Williams Channel Breakout dry-run 신호 서비스.

국내 `LarryWilliamsChannelBreakoutStrategy` 의 일봉 기반 진입 조건 중 해외 데이터로
재현 가능한 채널 돌파, 거래량, ADX 필터를 완성 일봉에 적용한다. 주문 경로는 없고
shadow 저널에 would-be 신호만 기록한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


@dataclass
class OverseasChannelBreakoutConfig:
    adx_period: int = 14
    adx_threshold: float = 25.0
    adx_slope_lookback: int = 3
    channel_high_period: int = 20
    channel_low_period: int = 10
    volume_multiplier: float = 1.5
    hard_stop_pct: float = -7.0
    partial_profit_trigger_pct: float = 15.0


class OverseasChannelBreakoutDryRunService:
    STRATEGY_NAME = "LarryWilliamsCB_overseas"
    SIGNAL_SOURCE = "overseas_cb_dryrun"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        indicator_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasChannelBreakoutConfig] = None,
        exchange: OverseasExchange = OverseasExchange.NASD,
        position_sizing_service=None,
        fx_provider=None,
    ) -> None:
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._indicator = indicator_service
        self._journal = shadow_journal
        self._logger = logger or logging.getLogger(__name__)
        self._cfg = config or OverseasChannelBreakoutConfig()
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
        """후보를 평가해 CB BUY would-be 신호를 반환하고(선택) shadow 저널에 기록한다."""
        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(
            ex, top_n=top_n, min_avg_trading_value=min_avg_trading_value
        )

        fx_rate = await self._resolve_fx_rate()
        signals: List[Dict[str, Any]] = []
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=60, exchange=ex)
            except Exception as e:
                self._logger.warning({"event": "overseas_cb_ohlcv_error", "code": code, "error": str(e)})
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
            "event": "overseas_cb_dryrun_scan",
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
            self._logger.warning({"event": "overseas_cb_fx_error", "error": str(e)})
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
        min_rows = max(
            self._cfg.channel_high_period,
            self._cfg.channel_low_period,
            self._cfg.adx_period + self._cfg.adx_slope_lookback,
        ) + 1
        if not rows or len(rows) < min_rows:
            return None

        cur = rows[-1]
        history = rows[:-1]
        close = self._f(cur.get("close"))
        volume = self._f(cur.get("volume"))
        if close <= 0 or volume <= 0:
            return None

        channel_high_rows = history[-self._cfg.channel_high_period:]
        channel_high = max((self._f(r.get("high")) for r in channel_high_rows), default=0.0)
        if channel_high <= 0 or close <= channel_high:
            return None

        avg_volume_rows = history[-self._cfg.channel_high_period:]
        volumes = [self._f(r.get("volume")) for r in avg_volume_rows if self._f(r.get("volume")) > 0]
        if len(volumes) < self._cfg.channel_high_period:
            return None
        avg_volume = sum(volumes) / len(volumes)
        if volume < avg_volume * self._cfg.volume_multiplier:
            return None

        adx_result = self._indicator.calc_adx_sync(
            history,
            period=self._cfg.adx_period,
            slope_lookback=self._cfg.adx_slope_lookback,
        )
        if not adx_result:
            return None
        adx = self._f(adx_result.get("adx"))
        if adx < self._cfg.adx_threshold or not adx_result.get("adx_rising"):
            return None

        channel_low_20d = min(
            (self._f(r.get("low")) for r in channel_high_rows if self._f(r.get("low")) > 0),
            default=0.0,
        )
        channel_low_rows = history[-self._cfg.channel_low_period:]
        channel_low = min(
            (self._f(r.get("low")) for r in channel_low_rows if self._f(r.get("low")) > 0),
            default=0.0,
        )
        price_stop = close * (1 + self._cfg.hard_stop_pct / 100)
        stop_price = max(channel_low_20d, price_stop)
        volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0
        target_price = close * (1 + self._cfg.partial_profit_trigger_pct / 100)

        return {
            "strategy": self.STRATEGY_NAME,
            "code": code,
            "name": candidate.get("name", code),
            "action": "BUY",
            "date": cur.get("date"),
            "entry_price": close,
            "entry_reason": "overseas_channel_breakout",
            "channel_high": channel_high,
            "channel_low_10d": channel_low,
            "stop_price": stop_price,
            "target_price": target_price,
            "volume": volume,
            "avg_volume": avg_volume,
            "volume_ratio": volume_ratio,
            "adx": adx,
            "reason": (
                f"CB진입(20일 채널 돌파 {close:.2f}>{channel_high:.2f}, "
                f"ADX={adx:.1f}, 거래량 {volume_ratio:.2f}x)"
            ),
        }

    @staticmethod
    def _bar_ohlc(bar: Dict[str, Any]) -> Dict[str, Any]:
        return {k: bar.get(k) for k in ("date", "open", "high", "low", "close", "volume")}

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
