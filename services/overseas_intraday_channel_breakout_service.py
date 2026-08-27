"""해외 장중 Channel Breakout (라이브 paper 경로).

dry-run(`OverseasChannelBreakoutDryRunService`)과 같은 진입 규칙을 장중 폴링으로
재현한다. CB 는 채널 상단·평균거래량·ADX 를 **완성봉**에서 뽑고 당일 봉에서는
종가/거래량만 쓰므로, 장중에는 폴링가와 누적거래량 환산으로 그대로 판정된다.

ADX 는 완성봉 기반이라 세션 내내 불변이다 — 틱마다 재계산하지 않고 세션 준비
시점에 한 번 걸러 감시 목록에서 제외한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradayChannelBreakoutConfig:
    adx_period: int = 14
    adx_threshold: float = 25.0
    adx_slope_lookback: int = 3
    channel_high_period: int = 20
    channel_low_period: int = 10
    volume_multiplier: float = 1.5
    hard_stop_pct: float = -7.0


class OverseasIntradayChannelBreakoutService(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "LarryWilliamsCB_overseas_intraday"
    EVENT_PREFIX = "overseas_intraday_cb"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        indicator_service,
        order_execution_service,
        session_volume_service=None,
        market_clock=None,
        position_sizing_service=None,
        market_regime_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasIntradayChannelBreakoutConfig] = None,
        top_n: int = 20,
        max_positions: int = 5,
        exchange: OverseasExchange = OverseasExchange.NASD,
        market_timing_gate: bool = True,
        state_file: Optional[str] = None,
    ) -> None:
        super().__init__(
            candidate_service=candidate_service,
            stock_query_service=stock_query_service,
            order_execution_service=order_execution_service,
            session_volume_service=session_volume_service,
            market_clock=market_clock,
            position_sizing_service=position_sizing_service,
            market_regime_service=market_regime_service,
            logger=logger,
            top_n=top_n,
            max_positions=max_positions,
            exchange=exchange,
            market_timing_gate=market_timing_gate,
            state_file=state_file,
        )
        self._indicator = indicator_service
        self._cfg = config or OverseasIntradayChannelBreakoutConfig()

    def _min_history(self) -> int:
        return max(
            self._cfg.channel_high_period,
            self._cfg.channel_low_period,
            self._cfg.adx_period + self._cfg.adx_slope_lookback,
        ) + 1

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        if not history or len(history) < self._min_history():
            return None

        channel_rows = history[-self._cfg.channel_high_period:]
        channel_high = max((self._f(r.get("high")) for r in channel_rows), default=0.0)
        if channel_high <= 0:
            return None

        volumes = [self._f(r.get("volume")) for r in channel_rows if self._f(r.get("volume")) > 0]
        if len(volumes) < self._cfg.channel_high_period:
            return None
        avg_volume = sum(volumes) / len(volumes)

        adx_result = self._indicator.calc_adx_sync(
            history, period=self._cfg.adx_period, slope_lookback=self._cfg.adx_slope_lookback,
        )
        if not adx_result:
            return None
        adx = self._f(adx_result.get("adx"))
        # ADX 는 완성봉 기반이라 장중 불변 — 여기서 거르면 틱마다 재판정할 필요가 없다.
        if adx < self._cfg.adx_threshold or not adx_result.get("adx_rising"):
            return None

        channel_low_20d = min(
            (self._f(r.get("low")) for r in channel_rows if self._f(r.get("low")) > 0), default=0.0,
        )
        channel_low_10d = min(
            (self._f(r.get("low")) for r in history[-self._cfg.channel_low_period:]
             if self._f(r.get("low")) > 0), default=0.0,
        )
        return {
            "channel_high": channel_high,
            "channel_low_20d": channel_low_20d,
            "channel_low_10d": channel_low_10d,
            "avg_volume": avg_volume,
            "adx": adx,
        }

    def _should_enter(self, setup, price, volume, now) -> bool:
        if price <= setup["channel_high"]:
            return False
        return self._volume_ok(setup, volume, now, multiplier=self._cfg.volume_multiplier)

    def _stop_price(self, setup, price) -> float:
        return max(setup["channel_low_20d"], price * (1 + self._cfg.hard_stop_pct / 100.0))

    def _entry_reason(self) -> str:
        return "cb_intraday_breakout"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        return {
            "channel_high": setup["channel_high"],
            "channel_low_10d": setup["channel_low_10d"],
            "adx": setup["adx"],
        }
