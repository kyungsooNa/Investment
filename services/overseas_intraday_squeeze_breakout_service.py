"""해외 장중 O'Neil Squeeze Breakout (라이브 paper 경로).

스퀴즈(볼린저 폭 수축)와 20일 고점은 **완성봉**에서 정해지므로 세션 준비 시점에
확정된다 — 스퀴즈가 아닌 종목은 감시 목록에서 제외한다. 장중 판정은 돌파·과연장·
거래량·캔들 상대위치 네 가지다.

캔들 상대위치는 관측된 세션 고/저를 쓴다(폴링 간격 사이 극값 미관측 — 베이스의
`_update_session_range` 주석 참고).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradaySqueezeBreakoutConfig:
    bollinger_period: int = 20
    squeeze_lookback: int = 20
    squeeze_tolerance: float = 1.2
    breakout_high_period: int = 20
    breakout_min_buffer_pct: float = 0.0
    max_extension_pct: float = 5.0
    volume_breakout_multiplier: float = 1.5
    min_candle_relative_pos: float = 0.7
    stop_loss_pct: float = -5.0


class OverseasIntradaySqueezeBreakoutService(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "O'NeilOSB_overseas_intraday"
    EVENT_PREFIX = "overseas_intraday_osb"

    def __init__(self, *args, config: Optional[OverseasIntradaySqueezeBreakoutConfig] = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = config or OverseasIntradaySqueezeBreakoutConfig()

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        need = self._cfg.bollinger_period + self._cfg.squeeze_lookback
        closes = [self._f(r.get("close")) for r in history]
        if len(closes) < need or any(v <= 0 for v in closes[-need:]):
            return None

        bb_width = self._bb_width(closes[-self._cfg.bollinger_period:])
        widths = [
            self._bb_width(closes[i - self._cfg.bollinger_period:i])
            for i in range(len(closes) - self._cfg.squeeze_lookback + 1, len(closes) + 1)
        ]
        widths = [w for w in widths if w > 0]
        if not widths or bb_width <= 0:
            return None
        # 스퀴즈는 완성봉으로 정해진다 — 아니면 세션 내내 아니므로 감시에서 제외한다.
        if bb_width > min(widths) * self._cfg.squeeze_tolerance:
            return None

        high_rows = history[-self._cfg.breakout_high_period:]
        breakout_level = max((self._f(r.get("high")) for r in high_rows), default=0.0)
        if breakout_level <= 0:
            return None

        volumes = [self._f(r.get("volume")) for r in high_rows]
        if len(volumes) < self._cfg.breakout_high_period or any(v <= 0 for v in volumes):
            return None

        return {
            "breakout_level": breakout_level,
            "breakout_threshold": breakout_level * (1 + self._cfg.breakout_min_buffer_pct / 100.0),
            "max_entry": breakout_level * (1 + self._cfg.max_extension_pct / 100.0),
            "avg_volume": sum(volumes) / len(volumes),
            "bb_width": bb_width,
        }

    def _should_enter(self, setup, price, volume, now) -> bool:
        if price < setup["breakout_threshold"]:
            return False
        if price > setup["max_entry"]:  # 과연장 진입 방지
            return False
        if self._relative_position(setup, price) < self._cfg.min_candle_relative_pos:
            return False
        return self._volume_ok(setup, volume, now,
                               multiplier=self._cfg.volume_breakout_multiplier)

    def _stop_price(self, setup, price) -> float:
        return price * (1 + self._cfg.stop_loss_pct / 100.0)

    def _entry_reason(self) -> str:
        return "osb_intraday_squeeze_breakout"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        return {"breakout_level": setup["breakout_level"], "bb_width": setup["bb_width"]}

    @staticmethod
    def _bb_width(closes: List[float]) -> float:
        if not closes:
            return 0.0
        mean = sum(closes) / len(closes)
        if mean <= 0:
            return 0.0
        var = sum((c - mean) ** 2 for c in closes) / len(closes)
        return (2 * math.sqrt(var) * 2) / mean
