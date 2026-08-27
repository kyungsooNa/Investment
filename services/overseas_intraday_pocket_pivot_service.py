"""해외 장중 Pocket Pivot (라이브 paper 경로).

지지 이동평균(10/20/50MA)과 하락일 최대거래량은 **완성봉**에서 정해지므로 세션
준비 시점에 확정된다. 장중 판정은 전일 종가 상회·MA 근접·캔들 상대위치·거래량이다.

거래량 조건이 dry-run 과 다른 점: dry-run 은 "당일 거래량 > 최근 하락일 최대거래량
× 0.9" 를 완성 거래량으로 보지만, 장중에는 확정값이 없어 환산 거래량으로 비교한다.
캔들 상대위치도 관측된 세션 고/저 기준이다(폴링 간격 사이 극값 미관측).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradayPocketPivotConfig:
    pp_ma_proximity_lower_pct: float = -2.0
    pp_ma_proximity_upper_pct: float = 4.0
    pp_down_day_lookback: int = 10
    pp_down_vol_threshold_ratio: float = 0.9
    pp_min_candle_relative_pos: float = 0.5
    pp_stop_loss_below_ma_pct: float = -2.0


class OverseasIntradayPocketPivotService(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "O'NeilPP_overseas_intraday"
    EVENT_PREFIX = "overseas_intraday_pp"

    def __init__(self, *args, config: Optional[OverseasIntradayPocketPivotConfig] = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = config or OverseasIntradayPocketPivotConfig()

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        closes = [self._f(r.get("close")) for r in history if self._f(r.get("close")) > 0]
        if len(closes) < 10 or not history:
            return None
        prev_close = self._f(history[-1].get("close"))
        if prev_close <= 0:
            return None

        recent = history[-self._cfg.pp_down_day_lookback:]
        down_day_volumes = [
            self._f(r.get("volume")) for r in recent
            if self._f(r.get("close")) < self._f(r.get("open")) and self._f(r.get("volume")) > 0
        ]
        if not down_day_volumes:
            return None

        return {
            "prev_close": prev_close,
            "ma_10d": sum(closes[-10:]) / 10,
            "ma_20d": sum(closes[-20:]) / 20 if len(closes) >= 20 else 0.0,
            "ma_50d": sum(closes[-50:]) / 50 if len(closes) >= 50 else 0.0,
            # 환산 거래량과 비교할 기준선 — 하락일 최대거래량의 90%
            "avg_volume": max(down_day_volumes) * self._cfg.pp_down_vol_threshold_ratio,
            "max_down_volume": max(down_day_volumes),
        }

    def _find_supporting_ma(self, price: float, setup: Dict[str, Any]) -> Tuple[str, float]:
        """현재가가 근접 구간에 있는 지지 MA 를 찾는다(짧은 것 우선)."""
        for name in ("ma_10d", "ma_20d", "ma_50d"):
            ma = self._f(setup.get(name))
            if ma <= 0:
                continue
            deviation = (price - ma) / ma * 100.0
            if self._cfg.pp_ma_proximity_lower_pct <= deviation <= self._cfg.pp_ma_proximity_upper_pct:
                return name, ma
        return "", 0.0

    def _should_enter(self, setup, price, volume, now) -> bool:
        if price <= setup["prev_close"]:
            return False
        name, ma = self._find_supporting_ma(price, setup)
        if not name:
            return False
        if self._relative_position(setup, price) < self._cfg.pp_min_candle_relative_pos:
            return False
        setup["_supporting_ma"] = name
        setup["_supporting_ma_value"] = ma
        # avg_volume 에 이미 0.9 배가 반영돼 있으므로 배수는 1.0 으로 둔다.
        return self._volume_ok(setup, volume, now, multiplier=1.0)

    def _stop_price(self, setup, price) -> float:
        ma = self._f(setup.get("_supporting_ma_value"))
        if ma <= 0:
            return price * (1 + self._cfg.pp_stop_loss_below_ma_pct / 100.0)
        return ma * (1 + self._cfg.pp_stop_loss_below_ma_pct / 100.0)

    def _entry_reason(self) -> str:
        return "pp_intraday_pocket_pivot"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        return {
            "supporting_ma": setup.get("_supporting_ma", ""),
            "supporting_ma_value": setup.get("_supporting_ma_value", 0.0),
            "max_down_volume": setup.get("max_down_volume", 0.0),
        }
