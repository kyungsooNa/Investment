"""미국장 Minervini Stage 2 전환 돌파 전략 (장중 paper 경로)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradayMinerviniStage2Config:
    transition_lookback_days: int = 10
    breakout_lookback_days: int = 20
    max_breakout_extension_pct: float = 5.0
    min_avg_trading_value_usd: float = 20_000_000.0
    min_intraday_trading_value_usd: float = 20_000_000.0
    hard_stop_pct: float = -5.0
    take_profit_pct: float = 4.0


class OverseasIntradayMinerviniStage2Service(OverseasIntradayStrategyBase):
    """최근 Stage 2 전환 종목의 20일 고점 돌파를 미국장 폴링으로 판정한다.

    미국 종목용 RS Rating 공급원이 아직 없으므로 Stage 분류에서는 RS 조건을
    생략하고, 유동성 상위 후보·미국장 국면 게이트로 품질을 보완한다.
    """

    STRATEGY_NAME = "MinerviniStage2Transition_overseas_intraday"
    STRATEGY_VERSION = "1"
    EVENT_PREFIX = "overseas_intraday_minervini_stage2"
    HISTORY_LIMIT = 260

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        order_execution_service,
        minervini_stage_service,
        session_volume_service=None,
        market_clock=None,
        position_sizing_service=None,
        market_regime_service=None,
        logger: Optional[logging.Logger] = None,
        *,
        config: Optional[OverseasIntradayMinerviniStage2Config] = None,
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
        self._stage_service = minervini_stage_service
        self._cfg = config or OverseasIntradayMinerviniStage2Config()

    def _build_setup(
        self,
        code: str,
        history: List[Dict[str, Any]],
        today_bar: Optional[Dict[str, Any]],
        trade_date: str,
    ) -> Optional[Dict[str, Any]]:
        minimum = 200 + self._cfg.transition_lookback_days
        if self._stage_service is None or len(history) < minimum:
            return None

        closes = [self._f(row.get("close")) for row in history]
        lows = [self._f(row.get("low")) for row in history]
        if any(value <= 0 for value in closes) or any(value <= 0 for value in lows):
            return None

        stages = self._stage_service.classify_stage_series(closes, lows, rs_rating=0)
        if not self._has_recent_stage2_transition(stages):
            return None

        recent = history[-self._cfg.breakout_lookback_days:]
        pivot = max((self._f(row.get("high")) for row in recent), default=0.0)
        avg_trading_value = sum(
            self._f(row.get("close")) * self._f(row.get("volume")) for row in recent
        ) / len(recent)
        if pivot <= 0 or avg_trading_value < self._cfg.min_avg_trading_value_usd:
            return None

        return {
            "pivot": pivot,
            "avg_trading_value_usd": avg_trading_value,
        }

    def _has_recent_stage2_transition(self, stages: List[int]) -> bool:
        lookback = self._cfg.transition_lookback_days
        if len(stages) < lookback + 1 or stages[-1] != 2:
            return False
        return any(stage != 2 for stage in stages[-(lookback + 1):-1])

    def _should_enter(self, setup, price, volume, now) -> bool:
        pivot = setup["pivot"]
        if price <= pivot or price > pivot * (1 + self._cfg.max_breakout_extension_pct / 100.0):
            return False
        if volume is None or volume <= 0:
            return False
        trading_value = price * volume
        setup["intraday_trading_value_usd"] = trading_value
        return trading_value >= self._cfg.min_intraday_trading_value_usd

    async def on_price(self, code: str, price: float, volume: Optional[float] = None):
        held = self._positions.get(code)
        if held is not None and price >= held["entry_price"] * (
            1 + self._cfg.take_profit_pct / 100.0
        ):
            held["last_price"] = price
            return await self._exit(code, price, reason="target")
        return await super().on_price(code, price, volume=volume)

    def _stop_price(self, setup, price) -> float:
        return price * (1 + self._cfg.hard_stop_pct / 100.0)

    def _entry_reason(self) -> str:
        return "minervini_stage2_transition_intraday_breakout"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        return {
            "pivot": setup["pivot"],
            "avg_trading_value_usd": setup["avg_trading_value_usd"],
            "intraday_trading_value_usd": setup.get("intraday_trading_value_usd", 0.0),
        }
